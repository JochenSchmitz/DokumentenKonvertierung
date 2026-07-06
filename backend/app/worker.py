"""Hintergrund-Worker: verarbeitet hochgeladene Dokumente der Reihe nach.

Ein Dokument durchläuft: pending -> processing -> done|error.
Pro Dokument werden die Seiten gerendert (pdftoppm), parallel durch das
Vision-Modell gelesen, als Markdown + Word in ergebnisse/ abgelegt und
mit Schlagworten/Zusammenfassung/Datum in der Datenbank angereichert.
"""

import asyncio
import datetime
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
import pypandoc
from sqlalchemy import select, update

from . import config, ocr
from .db import SessionLocal
from .models import DocStatus, Document, Page

log = logging.getLogger('worker')

IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.tif', '.tiff')

# Was der (einzige) Worker WIRKLICH gerade bearbeitet — Quelle der
# Wahrheit für die Statusanzeige, unabhängig vom DB-Status-Flag.
CURRENT: dict | None = None


def _page_count(pdf: Path) -> int:
    info = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True)
    for line in info.stdout.splitlines():
        if line.startswith('Pages:'):
            return int(line.split()[-1])
    raise RuntimeError('Seitenzahl nicht ermittelbar')


def _render_page(pdf: Path, page: int, tmpdir: str) -> bytes:
    out = Path(tmpdir) / f'seite-{page:03d}'
    subprocess.run(
        [
            'pdftoppm',
            '-png',
            '-r',
            str(config.OCR_DPI),
            '-f',
            str(page),
            '-l',
            str(page),
            '-singlefile',
            str(pdf),
            str(out),
        ],
        check=True,
    )
    return out.with_suffix('.png').read_bytes()


def _sanitize_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r'[^\w.§ ()-]', '_', stem, flags=re.UNICODE).strip()
    return stem[:120] or 'dokument'


def _write_results(doc: Document, texts: list[str]) -> str:
    """Markdown + docx in ergebnisse/ schreiben, liefert den Basisnamen."""
    stem = f'{_sanitize_stem(doc.filename)}__{doc.id.hex[:8]}'
    parts = [f'# {doc.filename}\n']
    for i, text in enumerate(texts, 1):
        parts.append(f'\n---\n\n## Seite {i}\n\n{text}\n')
    md_text = ''.join(parts)
    (config.RESULTS_DIR / f'{stem}.md').write_text(md_text)

    # Bild-Links entfernen: Konvertierung muss strikt offline bleiben
    md_safe = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'[Grafik: \1]', md_text)
    pypandoc.convert_text(
        md_safe,
        'docx',
        format='markdown',
        outputfile=str(config.RESULTS_DIR / f'{stem}.docx'),
        # <br> in Zellen als echte Umbrüche; Referenz-Vorlage mit
        # sichtbaren Tabellenrahmen (pandoc-Standard hat keine)
        extra_args=[
            '--lua-filter',
            str(Path(__file__).parent / 'pandoc-br.lua'),
            '--reference-doc',
            str(Path(__file__).parent / 'reference.docx'),
        ],
    )
    return stem


async def _process(doc_id) -> None:
    global CURRENT
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        original = config.ORIGINALS_DIR / doc.stored_name
        filename = doc.filename
    CURRENT = {'id': str(doc_id), 'filename': filename, 'pages': None}

    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(config.OCR_PARALLEL)

    async with httpx.AsyncClient() as client:
        with tempfile.TemporaryDirectory() as tmpdir:
            if original.suffix.lower() == '.pdf':
                n = await loop.run_in_executor(None, _page_count, original)
                images = [
                    await loop.run_in_executor(None, _render_page, original, p, tmpdir)
                    for p in range(1, n + 1)
                ]
            else:
                images = [original.read_bytes()]

        async def read_page(png: bytes) -> str:
            async with sem:
                return await ocr.ocr_page(client, png)

        CURRENT = {'id': str(doc_id), 'filename': filename, 'pages': len(images)}
        log.info('%s: %d Seiten -> Modell', filename, len(images))
        texts = await asyncio.gather(*(read_page(png) for png in images))
        meta = await ocr.extract_metadata(client, '\n\n'.join(texts))

    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        doc.result_stem = await loop.run_in_executor(
            None, _write_results, doc, list(texts)
        )
        doc.pages = [Page(page_no=i, content_md=t) for i, t in enumerate(texts, 1)]
        doc.page_count = len(texts)
        doc.tags = meta['tags']
        doc.summary = meta['summary']
        doc.doc_date = meta['doc_date']
        doc.status = DocStatus.done
        doc.error = None
        doc.processed_at = datetime.datetime.now(datetime.UTC)
        db.commit()
    log.info(
        '%s: fertig (%d Seiten, %d Schlagworte)',
        filename,
        len(texts),
        len(meta['tags']),
    )


def _claim_next() -> object | None:
    """Nächstes wartendes Dokument atomar auf 'processing' setzen.

    Sortierung uploaded_at + id: bei Sammel-Uploads mit identischem
    Zeitstempel bleibt die Reihenfolge trotzdem deterministisch (FIFO,
    UUID7-IDs sind zeitlich sortiert).
    """
    with SessionLocal() as db:
        doc = db.scalars(
            select(Document)
            .where(Document.status == DocStatus.pending)
            .order_by(Document.uploaded_at, Document.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if doc is None:
            return None
        doc.status = DocStatus.processing
        db.commit()
        return doc.id


def _recover_orphans() -> None:
    """Nach Neustart: unterbrochene Verarbeitungen zurück in die Queue."""
    with SessionLocal() as db:
        n = db.execute(
            update(Document)
            .where(Document.status == DocStatus.processing)
            .values(status=DocStatus.pending)
        ).rowcount
        db.commit()
    if n:
        log.info('%d unterbrochene Dokumente zurück in die Warteschlange', n)


async def worker_loop() -> None:
    global CURRENT
    log.info('Worker gestartet')
    _recover_orphans()
    while True:
        try:
            doc_id = _claim_next()
            if doc_id is None:
                # Selbstheilung: es gibt nur diesen einen Worker — wenn er
                # nichts bearbeitet, ist jedes 'processing' in der DB ein
                # verwaister Rest (z.B. nach Absturz) und wird requeued.
                CURRENT = None
                _recover_orphans()
                await asyncio.sleep(3)
                continue
            if not await ocr.is_model_up():
                log.warning(
                    'OCR-Modell (Port 8012) nicht erreichbar — '
                    'Dokument bleibt in Warteschlange'
                )
                with SessionLocal() as db:
                    db.get(Document, doc_id).status = DocStatus.pending
                    db.commit()
                await asyncio.sleep(15)
                continue
            try:
                await _process(doc_id)
            except Exception as exc:  # noqa: BLE001
                log.exception('Verarbeitung fehlgeschlagen')
                with SessionLocal() as db:
                    doc = db.get(Document, doc_id)
                    doc.status = DocStatus.error
                    doc.error = str(exc)[:2000]
                    db.commit()
            finally:
                CURRENT = None
        except Exception:  # noqa: BLE001
            log.exception('Worker-Schleife: unerwarteter Fehler')
            await asyncio.sleep(10)
