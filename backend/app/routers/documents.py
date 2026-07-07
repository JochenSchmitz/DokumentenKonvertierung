import asyncio
import hashlib
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
from ..schemas import DocumentDetail, DocumentOut, UploadResult, UploadSkipped

router = APIRouter(prefix='/api/documents', tags=['documents'])

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
    raw = info.filename.encode('cp437')
    for enc in ('utf-8', 'cp850'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _ingest_zip(
    db: Session, zip_name: str, stream: BinaryIO, seen: set[str]
) -> tuple[list[Document], list[UploadSkipped]]:
    """ZIP entpacken: jede Datei wird ein eigenes Dokument, die
    Ordnerstruktur im Archiv wird zu festen Schlagworten."""
    created: list[Document] = []
    skipped: list[UploadSkipped] = []
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile:
        return [], [UploadSkipped(filename=zip_name, reason='kein gültiges ZIP-Archiv')]
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = PurePosixPath(_fix_zip_name(info).replace('\\', '/'))
            # macOS-Metadaten im Archiv sind keine Dokumente
            if (
                '__MACOSX' in path.parts
                or path.name.startswith('._')
                or path.name == '.DS_Store'
            ):
                continue
            suffix = path.suffix.lower()
            if suffix == '.zip':
                skipped.append(
                    UploadSkipped(
                        filename=str(path),
                        reason='ZIP im ZIP wird nicht unterstützt',
                    )
                )
                continue
            if suffix not in ALLOWED_SUFFIXES:
                skipped.append(
                    UploadSkipped(
                        filename=str(path),
                        reason=f'Dateityp {suffix or "(ohne Endung)"} '
                        'wird nicht unterstützt',
                    )
                )
                continue
            folder_tags = [p.strip() for p in path.parts[:-1] if p.strip()]
            with archive.open(info) as member:
                doc, reason = _ingest(db, path.name, suffix, member, folder_tags, seen)
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

    stmt = select(Document).order_by(Document.uploaded_at.desc())

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

    # Anzeige-Wahrheit: 'processing' zeigt nur das Dokument, das der
    # (einzige) Worker laut eigener Auskunft WIRKLICH bearbeitet.
    # Verwaiste Flags (Absturz, Neustart) erscheinen als 'wartet',
    # bis die Selbstheilung sie requeued.
    current = worker.CURRENT
    result = []
    for doc in db.scalars(stmt).all():
        item = DocumentOut.model_validate(doc)
        if item.status == DocStatus.processing and (
            current is None or current.get('id') != str(doc.id)
        ):
            item.status = DocStatus.pending
        result.append(item)
    return result


def _get_document(doc_id: uuid.UUID, db: Session, with_pages: bool = False) -> Document:
    stmt = select(Document).where(Document.id == doc_id)
    if with_pages:
        stmt = stmt.options(selectinload(Document.pages))
    doc = db.scalars(stmt).first()
    if doc is None:
        raise HTTPException(404, 'Dokument nicht gefunden')
    return doc


@router.get('/{doc_id}', response_model=DocumentDetail)
def get_document(doc_id: uuid.UUID, db: SessionDep, user: auth.UserDep):
    return _get_document(doc_id, db, with_pages=True)


@router.post('/{doc_id}/reprocess', response_model=DocumentOut)
def reprocess(doc_id: uuid.UUID, db: SessionDep, user: auth.UserDep):
    doc = _get_document(doc_id, db, with_pages=True)
    if doc.status == DocStatus.processing:
        raise HTTPException(409, 'Dokument wird gerade verarbeitet')
    doc.pages = []
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
