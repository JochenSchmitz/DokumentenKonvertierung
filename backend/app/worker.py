"""Hintergrund-Worker: verarbeitet hochgeladene Dokumente der Reihe nach.

Ein Dokument durchläuft: pending -> processing -> done|error.
Je nach Dateityp: PDFs werden seitenweise gerendert (pdftoppm) und vom
Vision-Modell gelesen, Bilder gelesen bzw. beschrieben, .msg-E-Mails
und Word-Dateien (.doc/.docx) direkt in Text/Word überführt. Ergebnisse
landen als Markdown + Word in ergebnisse/, dazu Schlagworte/
Zusammenfassung/Datum in der Datenbank. Nicht lesbare Dokumente werden
nicht als Fehler liegengelassen, sondern bekommen ein Hinweis-.docx und
das Schlagwort 'Unlesbar'.
"""

import asyncio
import datetime
import io
import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import httpx
import pypandoc
from sqlalchemy import select, update

from . import config, ocr
from .db import SessionLocal
from .models import DocStatus, Document, Page

log = logging.getLogger('worker')

IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.tif', '.tiff')
UNREADABLE_TAG = 'Unlesbar'

# Was der (einzige) Worker WIRKLICH gerade bearbeitet — Quelle der
# Wahrheit für die Statusanzeige, unabhängig vom DB-Status-Flag.
CURRENT: dict | None = None


class UnreadableError(Exception):
    """Dokument ist defekt oder inhaltlich nicht sinnvoll verarbeitbar.

    Führt NICHT zu status=error (Retry zwecklos), sondern zu einem
    Hinweis-Ergebnis mit Schlagwort 'Unlesbar'.
    """


def pdf_page_count(pdf: Path) -> int:
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


def _strip_image_links(md: str) -> str:
    """Bild-Links durch Platzhalter ersetzen (Konvertierung bleibt offline)."""
    return re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'[Grafik: \1]', md)


def _doc_to_docx(src: Path, outdir: Path) -> Path:
    """Altes Word-Format per LibreOffice nach .docx konvertieren."""
    subprocess.run(
        [
            'soffice',
            '--headless',
            # Eigenes Profil: kollidiert nicht mit einer offenen
            # LibreOffice-Instanz des Benutzers
            f'-env:UserInstallation=file://{outdir}/lo-profil',
            '--convert-to',
            'docx',
            '--outdir',
            str(outdir),
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    out = outdir / f'{src.stem}.docx'
    if not out.exists():
        raise RuntimeError('LibreOffice hat keine .docx erzeugt')
    return out


def _docx_to_markdown(docx: Path) -> str:
    return _strip_image_links(pypandoc.convert_file(str(docx), 'markdown', 'docx'))


def _msg_to_markdown(path: Path) -> str:
    """Outlook-.msg als Markdown: Kopfdaten-Tabelle + Nachrichtentext."""
    import extract_msg

    msg = extract_msg.openMsg(str(path))
    try:
        rows = [
            ('Von', msg.sender),
            ('An', msg.to),
            ('Cc', msg.cc),
            ('Datum', str(msg.date) if msg.date else None),
            ('Betreff', msg.subject),
        ]
        parts = ['| Feld | Inhalt |', '|---|---|']
        for feld, wert in rows:
            if wert:
                clean = ' '.join(str(wert).split()).replace('|', '\\|')
                parts.append(f'| {feld} | {clean} |')
        body = (msg.body or '').strip()
        if not body and msg.htmlBody:
            html = msg.htmlBody
            if isinstance(html, bytes):
                html = html.decode('utf-8', errors='replace')
            body = _strip_image_links(
                pypandoc.convert_text(html, 'markdown', format='html')
            ).strip()
        anhaenge = [
            # getattr: eingebettete E-Mails als Anhang haben keinen Dateinamen
            getattr(a, 'longFilename', None)
            or getattr(a, 'shortFilename', None)
            or '(unbenannt)'
            for a in msg.attachments
        ]
        text = '\n'.join(parts) + '\n\n---\n\n' + body
        if anhaenge:
            text += '\n\n**Anhänge (nicht extrahiert):** ' + ', '.join(anhaenge)
        return text
    finally:
        msg.close()


def _sanitize_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r'[^\w.§ ()-]', '_', stem, flags=re.UNICODE).strip()
    return stem[:120] or 'dokument'


def _md_to_docx(md_text: str, outfile: Path) -> None:
    pypandoc.convert_text(
        md_text,
        'docx',
        format='markdown',
        outputfile=str(outfile),
        # <br> in Zellen als echte Umbrüche; Referenz-Vorlage mit
        # sichtbaren Tabellenrahmen (pandoc-Standard hat keine);
        # Dokumentsprache Deutsch, sonst stolpert die
        # Rechtschreibprüfung (en-US) über jedes Wort
        extra_args=[
            '--lua-filter',
            str(Path(__file__).parent / 'pandoc-br.lua'),
            '--reference-doc',
            str(Path(__file__).parent / 'reference.docx'),
            '--metadata=lang:de-DE',
        ],
    )


def _force_german_docx(docx: Path) -> None:
    """Dokumentsprache einer .docx auf Deutsch (de-DE) zwingen.

    Ersetzt nur das w:val-Attribut der w:lang-Elemente in styles.xml
    und document.xml — inhaltsschonend (kein Neuerzeugen), damit auch
    in OnlyOffice bearbeitete Dateien ihre Änderungen behalten.
    """
    buf = io.BytesIO()
    with (
        zipfile.ZipFile(docx) as src,
        zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename in ('word/styles.xml', 'word/document.xml'):
                data = re.sub(rb'(<w:lang\b[^>]*?w:val=")[^"]*(")', rb'\1de-DE\2', data)
            dst.writestr(info, data)
    docx.write_bytes(buf.getvalue())


def _write_results(
    doc: Document, texts: list[str], docx_source: Path | None = None
) -> str:
    """Markdown + docx in ergebnisse/ schreiben, liefert den Basisnamen.

    Ist docx_source gesetzt (Original war schon Word), wird diese Datei
    unverändert als Ergebnis-.docx übernommen statt aus dem Markdown
    neu erzeugt.
    """
    stem = f'{_sanitize_stem(doc.filename)}__{doc.id.hex[:8]}'
    parts = [f'# {doc.filename}\n']
    for i, text in enumerate(texts, 1):
        parts.append(f'\n---\n\n## Seite {i}\n\n{text}\n')
    md_text = ''.join(parts)
    (config.RESULTS_DIR / f'{stem}.md').write_text(md_text)

    if docx_source is not None:
        shutil.copyfile(docx_source, config.RESULTS_DIR / f'{stem}.docx')
        _force_german_docx(config.RESULTS_DIR / f'{stem}.docx')
    else:
        # Bild-Links entfernen: Konvertierung muss strikt offline bleiben
        _md_to_docx(_strip_image_links(md_text), config.RESULTS_DIR / f'{stem}.docx')
    return stem


def _merge_tags(fixed: list[str], new: list[str]) -> list[str]:
    return list(fixed) + [t for t in new if t not in fixed]


def _finish_unreadable(doc_id, reason: str) -> None:
    """Nicht lesbares Dokument abschließen: Hinweis-.docx + Tag 'Unlesbar'."""
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        note = (
            'Dieses Dokument konnte nicht gelesen oder nicht sinnvoll '
            f'verarbeitet werden.\n\n**Grund:** {reason}'
        )
        stem = f'{_sanitize_stem(doc.filename)}__{doc.id.hex[:8]}'
        md_text = f'# {doc.filename}\n\n{note}\n'
        (config.RESULTS_DIR / f'{stem}.md').write_text(md_text)
        _md_to_docx(md_text, config.RESULTS_DIR / f'{stem}.docx')
        doc.result_stem = stem
        doc.pages = [Page(page_no=1, content_md=note)]
        doc.page_count = 1
        doc.tags = _merge_tags([UNREADABLE_TAG], doc.fixed_tags or [])
        doc.summary = f'Nicht lesbar: {reason}'
        doc.status = DocStatus.done
        doc.error = None
        doc.processed_at = datetime.datetime.now(datetime.UTC)
        db.commit()


async def _read_page(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    filename: str,
    page_no: int,
    png: bytes,
    prompt: str | None = None,
) -> str:
    """Eine Seite lesen — mit Wiederholungen; ein endgültig
    fehlgeschlagener Einzelversuch reißt nicht mehr das ganze
    Dokument mit, sondern hinterlässt einen Platzhalter."""
    async with sem:
        for attempt in range(1, 4):
            try:
                return await ocr.ocr_page(client, png, prompt)
            except Exception as exc:  # noqa: BLE001
                reason = str(exc) or repr(exc)
                log.warning(
                    '%s Seite %d, Versuch %d/3: %s', filename, page_no, attempt, reason
                )
                if attempt == 3:
                    return f'[Seite {page_no}: Lesefehler nach 3 Versuchen — {reason}]'
                await asyncio.sleep(10)
        raise AssertionError('unreachable')


async def _extract_texts(
    client: httpx.AsyncClient,
    original: Path,
    filename: str,
    doc_id,
    tmpdir: str,
) -> tuple[list[str], Path | None]:
    """Dateityp-Weiche: liefert die Seitentexte und — falls das Original
    schon Word war — den Pfad der zu übernehmenden .docx."""
    global CURRENT
    loop = asyncio.get_running_loop()
    suffix = original.suffix.lower()

    if suffix == '.pdf':
        try:
            n = await loop.run_in_executor(None, pdf_page_count, original)
            images = [
                await loop.run_in_executor(None, _render_page, original, p, tmpdir)
                for p in range(1, n + 1)
            ]
        except (subprocess.SubprocessError, RuntimeError, OSError) as exc:
            raise UnreadableError(
                f'PDF ist defekt oder nicht renderbar ({exc or exc!r})'
            ) from exc
        CURRENT = {'id': str(doc_id), 'filename': filename, 'pages': len(images)}
        log.info('%s: %d Seiten -> Modell', filename, len(images))
        sem = asyncio.Semaphore(config.OCR_PARALLEL)
        texts = await asyncio.gather(
            *(
                _read_page(client, sem, filename, i, png)
                for i, png in enumerate(images, 1)
            )
        )
        return list(texts), None

    if suffix in IMAGE_SUFFIXES:
        try:
            png = original.read_bytes()
        except OSError as exc:
            raise UnreadableError(f'Bilddatei nicht lesbar ({exc})') from exc
        CURRENT = {'id': str(doc_id), 'filename': filename, 'pages': 1}
        log.info('%s: Bild -> Modell (lesen oder beschreiben)', filename)
        sem = asyncio.Semaphore(1)
        text = await _read_page(client, sem, filename, 1, png, config.IMAGE_PROMPT)
        return [text], None

    if suffix == '.msg':
        CURRENT = {'id': str(doc_id), 'filename': filename, 'pages': 1}
        try:
            text = await loop.run_in_executor(None, _msg_to_markdown, original)
        except Exception as exc:  # noqa: BLE001
            raise UnreadableError(
                f'Outlook-Nachricht (.msg) nicht lesbar ({exc or exc!r})'
            ) from exc
        return [text], None

    if suffix in ('.doc', '.docx'):
        CURRENT = {'id': str(doc_id), 'filename': filename, 'pages': 1}
        docx_source = original
        if suffix == '.doc':
            try:
                docx_source = await loop.run_in_executor(
                    None, _doc_to_docx, original, Path(tmpdir)
                )
            except Exception as exc:  # noqa: BLE001
                raise UnreadableError(
                    f'.doc-Konvertierung fehlgeschlagen ({exc or exc!r})'
                ) from exc
        try:
            text = await loop.run_in_executor(None, _docx_to_markdown, docx_source)
        except Exception as exc:  # noqa: BLE001
            raise UnreadableError(f'Word-Datei nicht lesbar ({exc or exc!r})') from exc
        return [text], docx_source

    raise UnreadableError(f'Dateityp {suffix} kann nicht verarbeitet werden')


async def _process(doc_id) -> None:
    global CURRENT
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        original = config.ORIGINALS_DIR / doc.stored_name
        filename = doc.filename
        fixed_tags = list(doc.fixed_tags or [])
    CURRENT = {'id': str(doc_id), 'filename': filename, 'pages': None}
    loop = asyncio.get_running_loop()

    try:
        async with httpx.AsyncClient() as client:
            with tempfile.TemporaryDirectory() as tmpdir:
                texts, docx_source = await _extract_texts(
                    client, original, filename, doc_id, tmpdir
                )
                # Ohne jeden lesbaren Text ist auch das Ergebnis wertlos —
                # außer das Original war schon Word (dann übernehmen wir es).
                if docx_source is None and not any(t.strip() for t in texts):
                    raise UnreadableError('kein lesbarer Inhalt gefunden')
                full_text = '\n\n'.join(texts)
                if full_text.strip():
                    meta = await ocr.extract_metadata(client, full_text)
                else:
                    meta = {'tags': [], 'summary': None, 'doc_date': None}

                with SessionLocal() as db:
                    doc = db.get(Document, doc_id)
                    doc.result_stem = await loop.run_in_executor(
                        None, _write_results, doc, list(texts), docx_source
                    )
                    doc.pages = [
                        Page(page_no=i, content_md=t) for i, t in enumerate(texts, 1)
                    ]
                    doc.page_count = len(texts)
                    doc.tags = _merge_tags(fixed_tags, meta['tags'])
                    doc.summary = meta['summary']
                    doc.doc_date = meta['doc_date']
                    doc.status = DocStatus.done
                    doc.error = None
                    doc.processed_at = datetime.datetime.now(datetime.UTC)
                    db.commit()
    except UnreadableError as exc:
        log.warning('%s: unlesbar — %s', filename, exc)
        await loop.run_in_executor(None, _finish_unreadable, doc_id, str(exc))
        return
    log.info('%s: fertig (%d Seiten)', filename, len(texts))


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
                    # str(exc) kann leer sein (z.B. httpx.ReadTimeout)
                    doc.error = (str(exc) or repr(exc))[:2000]
                    db.commit()
            finally:
                CURRENT = None
        except Exception:  # noqa: BLE001
            log.exception('Worker-Schleife: unerwarteter Fehler')
            await asyncio.sleep(10)
