import asyncio
import hashlib
import logging
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Cookie, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import auth, config
from ..db import SessionDep
from ..models import DocStatus, Document, Page
from ..schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentUpdate,
    UploadResult,
    UploadSkipped,
)

router = APIRouter(prefix='/api/documents', tags=['documents'])
log = logging.getLogger('upload')

ALLOWED_SUFFIXES = {
    '.pdf',
    '.png',
    '.jpg',
    '.jpeg',
    '.tif',
    '.tiff',
    '.msg',
    '.doc',
    '.docx',
    '.zip',
}
MIME_BY_SUFFIX = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
    '.msg': 'application/vnd.ms-outlook',
    '.doc': 'application/msword',
    '.docx': (
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    ),
}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff'}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _ingest(
    db: Session,
    filename: str,
    suffix: str,
    stream: BinaryIO,
    fixed_tags: list[str],
    seen: set[str],
) -> tuple[Document | None, str | None]:
    """Eine Datei speichern — liefert (Dokument, None) oder (None, Grund).

    Der SHA-256 wird beim Wegschreiben mitgerechnet; Duplikate (gegen die
    Datenbank UND innerhalb desselben Uploads) werden abgelehnt und die
    gerade geschriebene Datei wieder entfernt.
    """
    doc_id = uuid.uuid7()
    stored = f'{doc_id}{suffix}'
    target = config.ORIGINALS_DIR / stored
    h = hashlib.sha256()
    with target.open('wb') as out:
        while chunk := stream.read(1 << 20):
            h.update(chunk)
            out.write(chunk)
    sha = h.hexdigest()

    if sha in seen:
        target.unlink()
        return None, 'mehrfach im selben Upload enthalten'
    existing = db.scalars(
        select(Document).where(Document.sha256 == sha).limit(1)
    ).first()
    if existing is not None:
        target.unlink()
        return None, f'bereits vorhanden als „{existing.filename}“'
    seen.add(sha)

    # Seitenzahl schon beim Upload, damit die Warteschlange sie
    # anzeigen kann; bei kaputtem PDF bleibt sie leer, der Worker
    # meldet den Fehler dann bei der Verarbeitung.
    page_count = None
    if suffix == '.pdf':
        from ..worker import pdf_page_count

        try:
            page_count = pdf_page_count(target)
        except Exception:  # noqa: BLE001
            page_count = None
    elif suffix in IMAGE_SUFFIXES:
        page_count = 1  # Bilder liest der Worker als genau eine Seite

    doc = Document(
        id=doc_id,
        filename=filename,
        stored_name=stored,
        mime=MIME_BY_SUFFIX[suffix],
        size_bytes=target.stat().st_size,
        sha256=sha,
        status=DocStatus.pending,
        page_count=page_count,
        tags=list(fixed_tags),  # Ordner-Tags sofort sichtbar
        fixed_tags=list(fixed_tags),
    )
    db.add(doc)
    return doc, None


def _fix_zip_name(info: zipfile.ZipInfo) -> str:
    """Umlaute in ZIP-Namen reparieren.

    Ohne UTF-8-Flag dekodiert zipfile die Namen als cp437; Windows-
    Archive sind aber meist UTF-8 oder cp850 ("Prüfbericht" statt
    "Pr³fbericht").
    """
    if info.flag_bits & 0x800:
        return info.filename
    return _decode_zip_name(info.filename.encode('cp437'))


def _decode_zip_name(raw: bytes) -> str:
    for enc in ('utf-8', 'cp850'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('cp437', errors='replace')


def _is_zip_junk(path: PurePosixPath) -> bool:
    """macOS-Metadaten im Archiv sind keine Dokumente."""
    return (
        '__MACOSX' in path.parts
        or path.name.startswith('._')
        or path.name == '.DS_Store'
    )


def _check_member_suffix(path: PurePosixPath) -> UploadSkipped | None:
    suffix = path.suffix.lower()
    if suffix == '.zip':
        return UploadSkipped(
            filename=str(path), reason='ZIP im ZIP wird nicht unterstützt'
        )
    if suffix not in ALLOWED_SUFFIXES:
        return UploadSkipped(
            filename=str(path),
            reason=f'Dateityp {suffix or "(ohne Endung)"} wird nicht unterstützt',
        )
    return None


def _folder_tags(path: PurePosixPath) -> list[str]:
    return [p.strip() for p in path.parts[:-1] if p.strip()]


def _zip_diagnosis(stream: BinaryIO, exc: Exception) -> str:
    """Möglichst konkreter Grund, warum ein ZIP nicht lesbar ist."""
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    head = stream.read(4)
    stream.seek(0)
    if size == 0:
        return (
            'Datei ist leer (0 Bytes) — vermutlich ein nicht heruntergeladener '
            'Cloud-Platzhalter oder ein abgebrochener Download'
        )
    if not head.startswith(b'PK'):
        return (
            f'keine ZIP-Signatur am Dateianfang ({size} Bytes) — '
            'die Datei ist kein ZIP-Archiv oder beschädigt'
        )
    return f'ZIP-Archiv ist defekt oder unvollständig ({size} Bytes; {exc})'


class _ChunkStream:
    """Macht aus dem Chunk-Generator von stream-unzip ein read()-Objekt."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self._buf = bytearray()

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self._buf) < n:
            chunk = next(self._chunks, None)
            if chunk is None:
                break
            self._buf.extend(chunk)
        if n < 0:
            n = len(self._buf)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


def _ingest_zip_fallback(
    db: Session, zip_name: str, stream: BinaryIO, seen: set[str], bad: Exception
) -> tuple[list[Document], list[UploadSkipped]]:
    """Rettungsweg für Archive, die zipfile ablehnt.

    OneDrive/SharePoint erzeugen bei Sammel-Downloads über 4 GB formal
    defekte ZIPs (kaputtes zentrales Verzeichnis). stream-unzip liest —
    wie 7-Zip — die lokalen Dateiköpfe sequenziell und kommt damit
    trotzdem an den Inhalt. Bricht das Archiv mittendrin ab, bleiben
    die bis dahin geretteten Dokumente erhalten.
    """
    from stream_unzip import stream_unzip

    created: list[Document] = []
    skipped: list[UploadSkipped] = []
    stream.seek(0)

    def chunks():
        while chunk := stream.read(1 << 20):
            yield chunk

    try:
        for raw_name, _size, data in stream_unzip(chunks()):
            name = _decode_zip_name(raw_name).replace('\\', '/')
            path = PurePosixPath(name)
            if name.endswith('/') or _is_zip_junk(path):
                for _ in data:  # Eintrag vollständig überspringen
                    pass
                continue
            skip = _check_member_suffix(path)
            if skip is not None:
                skipped.append(skip)
                for _ in data:
                    pass
                continue
            doc, reason = _ingest(
                db,
                path.name,
                path.suffix.lower(),
                _ChunkStream(data),
                _folder_tags(path),
                seen,
            )
            if doc is not None:
                created.append(doc)
            else:
                skipped.append(UploadSkipped(filename=str(path), reason=reason or ''))
    except Exception as exc:  # noqa: BLE001 — defektes Archiv: Teilergebnis behalten
        log.warning('ZIP %s: Fallback-Lesen abgebrochen: %r', zip_name, exc)
        if created:
            skipped.append(
                UploadSkipped(
                    filename=zip_name,
                    reason=f'Archiv defekt — nach {len(created)} geretteten '
                    f'Dateien abgebrochen ({str(exc) or type(exc).__name__})',
                )
            )
        else:
            skipped.append(
                UploadSkipped(filename=zip_name, reason=_zip_diagnosis(stream, bad))
            )
    return created, skipped


def _ingest_zip(
    db: Session, zip_name: str, stream: BinaryIO, seen: set[str]
) -> tuple[list[Document], list[UploadSkipped]]:
    """ZIP entpacken: jede Datei wird ein eigenes Dokument, die
    Ordnerstruktur im Archiv wird zu festen Schlagworten."""
    created: list[Document] = []
    skipped: list[UploadSkipped] = []
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        log.warning(
            'ZIP %s: zipfile scheitert (%s) — versuche stream-unzip', zip_name, exc
        )
        return _ingest_zip_fallback(db, zip_name, stream, seen, exc)
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = PurePosixPath(_fix_zip_name(info).replace('\\', '/'))
            if _is_zip_junk(path):
                continue
            skip = _check_member_suffix(path)
            if skip is not None:
                skipped.append(skip)
                continue
            with archive.open(info) as member:
                doc, reason = _ingest(
                    db, path.name, path.suffix.lower(), member, _folder_tags(path), seen
                )
            if doc is not None:
                created.append(doc)
            else:
                skipped.append(UploadSkipped(filename=str(path), reason=reason or ''))
    return created, skipped


@router.post('', response_model=UploadResult)
async def upload(files: list[UploadFile], db: SessionDep, user: auth.UserDep):
    """Dateien annehmen; ZIPs werden entpackt, Duplikate (gleicher
    Inhalt, per SHA-256) mit kurzem Hinweis abgelehnt statt gespeichert."""
    created: list[Document] = []
    skipped: list[UploadSkipped] = []
    seen: set[str] = set()
    for f in files:
        name = f.filename or 'datei'
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            skipped.append(
                UploadSkipped(
                    filename=name,
                    reason=f'Dateityp {suffix or "(ohne Endung)"} '
                    'wird nicht unterstützt',
                )
            )
            continue
        if suffix == '.zip':
            zip_created, zip_skipped = await asyncio.to_thread(
                _ingest_zip, db, name, f.file, seen
            )
            created.extend(zip_created)
            skipped.extend(zip_skipped)
        else:
            doc, reason = await asyncio.to_thread(
                _ingest, db, name, suffix, f.file, [], seen
            )
            if doc is not None:
                created.append(doc)
            else:
                skipped.append(UploadSkipped(filename=name, reason=reason or ''))
    db.commit()
    return UploadResult(
        created=[DocumentOut.model_validate(d) for d in created], skipped=skipped
    )


@router.get('', response_model=list[DocumentOut])
def list_documents(db: SessionDep, user: auth.UserDep, q: str = '', tags: str = ''):
    """Dokumentliste, optional gefiltert per Trigram-Suche (pg_trgm).

    Durchsucht Dateiname, Zusammenfassung, Schlagworte und den
    OCR-Volltext aller Seiten. Der Vergleich ist leerzeichen-
    unempfindlich: Query und Text werden ohne Whitespace verglichen,
    daher findet "ad blue" auch "AdBlue" und "Gewähr Leistung" auch
    "Gewährleistung". Die GIN-Trigram-Expression-Indizes halten die
    ILIKE-'%...%'-Suchen auch bei großen Beständen schnell.
    """
    from .. import worker

    stmt = (
        select(Document)
        .options(selectinload(Document.entities))
        .order_by(Document.uploaded_at.desc())
    )

    # Zusätzlicher Tag-Filter (kommagetrennt): Dokument muss ALLE
    # angeklickten Schlagworte tragen (Postgres-Array-Contains @>).
    tag_list = [t for t in (s.strip() for s in tags.split(',')) if t]
    if tag_list:
        stmt = stmt.where(Document.tags.contains(tag_list))

    q = ''.join(q.split())  # Whitespace aus der Query entfernen
    if q:
        escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like = f'%{escaped}%'

        def squeezed(col):
            # Muss exakt dem Ausdruck der Expression-Indizes entsprechen
            return func.regexp_replace(col, '\\s', '', 'g')

        page_match = (
            select(Page.id)
            .where(
                Page.document_id == Document.id,
                squeezed(Page.content_md).ilike(like),
            )
            .exists()
        )
        stmt = stmt.where(
            or_(
                squeezed(Document.filename).ilike(like),
                squeezed(Document.summary).ilike(like),
                squeezed(func.array_to_string(Document.tags, ' ')).ilike(like),
                page_match,
            )
        )

    # Anzeige-Wahrheit: 'processing' zeigt nur die Dokumente, die der Worker
    # laut eigener Auskunft (worker.CURRENT) WIRKLICH gerade bearbeitet.
    # Verwaiste Flags (Absturz, Neustart) erscheinen als 'wartet', bis die
    # Selbstheilung beim nächsten Start sie requeued.
    current = worker.CURRENT
    result = []
    for doc in db.scalars(stmt).all():
        item = DocumentOut.model_validate(doc)
        if item.status == DocStatus.processing and str(doc.id) not in current:
            item.status = DocStatus.pending
        result.append(item)
    return result


def _get_document(doc_id: uuid.UUID, db: Session, with_pages: bool = False) -> Document:
    stmt = select(Document).where(Document.id == doc_id)
    if with_pages:
        stmt = stmt.options(
            selectinload(Document.pages), selectinload(Document.entities)
        )
    doc = db.scalars(stmt).first()
    if doc is None:
        raise HTTPException(404, 'Dokument nicht gefunden')
    return doc


@router.get('/{doc_id}', response_model=DocumentDetail)
def get_document(doc_id: uuid.UUID, db: SessionDep, user: auth.UserDep):
    return _get_document(doc_id, db, with_pages=True)


@router.patch('/{doc_id}', response_model=DocumentOut)
def update_document(
    doc_id: uuid.UUID, patch: DocumentUpdate, db: SessionDep, user: auth.UserDep
):
    """Schlagworte von Hand pflegen (in der Bearbeitungsansicht).

    Die Liste wird normalisiert: getrimmt, Leereinträge und Dubletten
    entfernt, Reihenfolge bleibt erhalten. Achtung: bei einer erneuten
    Verarbeitung (`reprocess`) überschreibt das Modell die Tags wieder
    (feste Ordner-Tags bleiben, manuelle Ergänzungen gehen verloren).
    """
    doc = _get_document(doc_id, db)
    seen: set[str] = set()
    clean: list[str] = []
    for raw in patch.tags:
        tag = raw.strip()
        if tag and tag not in seen:
            seen.add(tag)
            clean.append(tag)
    doc.tags = clean
    db.commit()
    db.refresh(doc)
    return doc


@router.post('/{doc_id}/reprocess', response_model=DocumentOut)
def reprocess(doc_id: uuid.UUID, db: SessionDep, user: auth.UserDep):
    doc = _get_document(doc_id, db, with_pages=True)
    if doc.status == DocStatus.processing:
        raise HTTPException(409, 'Dokument wird gerade verarbeitet')
    doc.pages = []
    # Abgeleitete Beteiligte verwerfen; die Neuverarbeitung erzeugt sie neu.
    doc.entities = []
    doc.entities_at = None
    doc.status = DocStatus.pending
    doc.error = None
    db.commit()
    return doc


@router.delete('/{doc_id}', status_code=204)
def delete_document(doc_id: uuid.UUID, db: SessionDep, user: auth.UserDep):
    doc = _get_document(doc_id, db)
    (config.ORIGINALS_DIR / doc.stored_name).unlink(missing_ok=True)
    if doc.result_stem:
        for ext in ('.md', '.docx'):
            (config.RESULTS_DIR / f'{doc.result_stem}{ext}').unlink(missing_ok=True)
    db.delete(doc)
    db.commit()


@router.get('/{doc_id}/file/original')
def file_original(
    doc_id: uuid.UUID,
    db: SessionDep,
    session: Annotated[str | None, Cookie()] = None,
    token: str | None = None,
):
    auth.check_file_access(doc_id, session, token)
    doc = _get_document(doc_id, db)
    path = config.ORIGINALS_DIR / doc.stored_name
    if not path.exists():
        raise HTTPException(404, 'Originaldatei fehlt')
    return FileResponse(path, media_type=doc.mime, filename=doc.filename)


@router.get('/{doc_id}/file/{fmt}')
def file_result(
    doc_id: uuid.UUID,
    fmt: str,
    db: SessionDep,
    session: Annotated[str | None, Cookie()] = None,
    token: str | None = None,
):
    auth.check_file_access(doc_id, session, token)
    if fmt not in ('docx', 'md'):
        raise HTTPException(404, 'Unbekanntes Format')
    doc = _get_document(doc_id, db)
    if not doc.result_stem:
        raise HTTPException(409, 'Dokument ist noch nicht verarbeitet')
    path = config.RESULTS_DIR / f'{doc.result_stem}.{fmt}'
    if not path.exists():
        raise HTTPException(404, 'Ergebnisdatei fehlt')
    media = (
        ('application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        if fmt == 'docx'
        else 'text/markdown'
    )
    stem = Path(doc.filename).stem
    return FileResponse(path, media_type=media, filename=f'{stem}.{fmt}')
