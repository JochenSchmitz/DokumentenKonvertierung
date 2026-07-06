import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import auth, config
from ..db import SessionDep
from ..models import DocStatus, Document, Page
from ..schemas import DocumentDetail, DocumentOut

router = APIRouter(prefix='/api/documents', tags=['documents'])

ALLOWED_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}
MIME_BY_SUFFIX = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
}


@router.post('', response_model=list[DocumentOut])
async def upload(files: list[UploadFile], db: SessionDep, user: auth.UserDep):
    created: list[Document] = []
    for f in files:
        suffix = Path(f.filename or 'datei').suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                415,
                f'Dateityp {suffix or "(ohne Endung)"} wird nicht '
                f'unterstützt: {f.filename}',
            )
        doc_id = uuid.uuid7()
        stored = f'{doc_id}{suffix}'
        target = config.ORIGINALS_DIR / stored
        with target.open('wb') as out:
            shutil.copyfileobj(f.file, out)
        doc = Document(
            id=doc_id,
            filename=f.filename or stored,
            stored_name=stored,
            mime=MIME_BY_SUFFIX[suffix],
            size_bytes=target.stat().st_size,
            status=DocStatus.pending,
        )
        db.add(doc)
        created.append(doc)
    db.commit()
    return created


@router.get('', response_model=list[DocumentOut])
def list_documents(db: SessionDep, user: auth.UserDep, q: str = ''):
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
