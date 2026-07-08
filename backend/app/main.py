import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, ocr
from .auth import UserDep
from .db import Base, SessionDep, engine
from .routers import auth, documents, onlyoffice

logging.basicConfig(
    level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)


def _migrate(connection) -> None:
    """Schema-Nachzügler: create_all ergänzt keine Spalten in
    bestehenden Tabellen, daher hier idempotente ALTERs."""
    from sqlalchemy import text

    for ddl in (
        'ALTER TABLE documents ADD COLUMN IF NOT EXISTS sha256 text',
        'ALTER TABLE documents ADD COLUMN IF NOT EXISTS fixed_tags '
        "text[] NOT NULL DEFAULT '{}'",
        'ALTER TABLE documents ADD COLUMN IF NOT EXISTS entities_at timestamptz',
        'CREATE INDEX IF NOT EXISTS ix_documents_sha256 ON documents (sha256)',
    ):
        connection.execute(text(ddl))
    connection.commit()


def _backfill_hashes() -> None:
    """SHA-256 für Alt-Dokumente nachtragen (einmalig, idempotent)."""
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Document
    from .routers.documents import file_sha256

    with SessionLocal() as db:
        docs = db.scalars(select(Document).where(Document.sha256.is_(None))).all()
        done = 0
        for doc in docs:
            path = config.ORIGINALS_DIR / doc.stored_name
            if not path.exists():
                continue
            doc.sha256 = file_sha256(path)
            done += 1
        db.commit()
    if done:
        logging.getLogger('main').info('Hash-Backfill: %d Dokumente ergänzt', done)


def _init_search(connection) -> None:
    """Trigram-Suche: Extension + GIN-Indizes für ILIKE '%...%'-Suchen."""
    from sqlalchemy import text

    connection.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
    for ddl in (
        # Alte, zeichengenaue Indizes durch Expression-Indizes auf dem
        # whitespace-bereinigten Text ersetzen: die Suche entfernt in
        # Query UND Text alle Leerzeichen ("ad blue" findet "AdBlue",
        # "Gewähr Leistung" findet "Gewährleistung").
        'DROP INDEX IF EXISTS ix_documents_filename_trgm',
        'DROP INDEX IF EXISTS ix_documents_summary_trgm',
        'DROP INDEX IF EXISTS ix_pages_content_trgm',
        'CREATE INDEX IF NOT EXISTS ix_documents_filename_trgm_ws '
        'ON documents USING gin '
        "((regexp_replace(filename, '\\s', '', 'g')) gin_trgm_ops)",
        'CREATE INDEX IF NOT EXISTS ix_documents_summary_trgm_ws '
        'ON documents USING gin '
        "((regexp_replace(summary, '\\s', '', 'g')) gin_trgm_ops)",
        'CREATE INDEX IF NOT EXISTS ix_pages_content_trgm_ws '
        'ON pages USING gin '
        "((regexp_replace(content_md, '\\s', '', 'g')) gin_trgm_ops)",
    ):
        connection.execute(text(ddl))
    connection.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        _migrate(connection)
        _init_search(connection)
    await asyncio.to_thread(_backfill_hashes)
    # WORKER_ENABLED=0 z.B. in Tests: dort darf kein Worker gegen die
    # echte Datenbank laufen (er würde Dokumente aus der Queue claimen
    # und beim Testende verwaiste processing-Flags hinterlassen).
    task = None
    if os.environ.get('WORKER_ENABLED', '1') == '1':
        from .worker import worker_loop

        task = asyncio.create_task(worker_loop())
    yield
    if task is not None:
        task.cancel()


app = FastAPI(title='FDS — FES Dokumentenservice', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # internes LAN-Tool; bei Bedarf einschränken
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(onlyoffice.router)


async def _onlyoffice_public_url() -> str:
    """OnlyOffice-URL für den Browser.

    Läuft ein ngrok-Tunnel auf Port 8082, dessen (dynamische) HTTPS-URL
    zurückgeben — nötig, wenn die Seite selbst über ngrok/HTTPS läuft,
    weil der Browser das OnlyOffice-Script sonst nicht laden darf
    (Mixed Content). Fallback: die LAN-URL aus der Konfiguration.
    """
    import os

    import httpx

    ngrok_api = os.environ.get('NGROK_API_URL', 'http://localhost:4043')
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f'{ngrok_api}/api/tunnels', timeout=1)
            for tunnel in resp.json().get('tunnels', []):
                if tunnel.get('config', {}).get('addr', '').endswith(':8082'):
                    return tunnel['public_url']
    except Exception:  # noqa: BLE001
        pass
    return config.ONLYOFFICE_URL


async def _vllm_counters() -> dict:
    """Lebenszeichen des Modells aus den vLLM-Prometheus-Metriken."""
    import httpx

    counters = {
        'runningRequests': 0,
        'waitingRequests': 0,
        'generatedTokens': 0,
        'promptTokens': 0,
    }
    wanted = {
        'vllm:num_requests_running': 'runningRequests',
        'vllm:num_requests_waiting{': 'waitingRequests',
        'vllm:generation_tokens_total': 'generatedTokens',
        'vllm:prompt_tokens_total': 'promptTokens',
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                config.VLLM_URL.removesuffix('/v1') + '/metrics', timeout=2
            )
        for line in resp.text.splitlines():
            for prefix, key in wanted.items():
                if line.startswith(prefix):
                    counters[key] += int(float(line.rsplit(' ', 1)[-1]))
    except httpx.HTTPError, ValueError:
        pass
    return counters


@app.get('/api/status')
async def processing_status(user: UserDep, db: SessionDep):
    """Verarbeitungs-Status für die Live-Anzeige im Frontend.

    "Liest gerade" kommt direkt vom Worker (worker.CURRENT), nicht vom
    Status-Flag in der DB — das Flag kann nach einem Absturz kurzzeitig
    veraltet sein, der Worker weiß immer, woran er wirklich arbeitet.
    """
    from sqlalchemy import func, select

    from . import worker
    from .models import DocStatus, Document

    current = worker.CURRENT
    pending = db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.status.in_((DocStatus.pending, DocStatus.processing)))
    ) - (1 if current else 0)
    return {
        'processing': [current['filename']] if current else [],
        'currentPages': current['pages'] if current else None,
        'pending': max(pending, 0),
        'modelUp': await ocr.is_model_up(),
        **await _vllm_counters(),
    }


@app.get('/api/version')
def version():
    """Öffentlich: App-Version aus der VERSION-Datei (auch für commit.sh)."""
    try:
        v = (config.PROJECT_DIR / 'VERSION').read_text().strip()
    except OSError:
        v = '0.0.0'
    return {'version': v}


@app.get('/api/config')
async def frontend_config(user: UserDep):
    return {
        'onlyofficeUrl': await _onlyoffice_public_url(),
        'apiBaseUrl': config.PUBLIC_BASE_URL,
        'ocrModelUp': await ocr.is_model_up(),
    }


class _SpaStaticFiles(StaticFiles):
    """SPA-Fallback: unbekannte Pfade (z.B. /upload, /edit/<id>) liefern
    index.html aus, damit Deep-Links und Browser-Reloads funktionieren —
    das Routing übernimmt dann vue-router im Browser.

    Starlette WIRFT bei fehlenden Dateien eine HTTPException(404)
    (statt eine 404-Response zurückzugeben), daher try/except.
    """

    async def get_response(self, path: str, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response('index.html', scope)
            raise


# Gebautes Frontend (frontend/dist) direkt mit ausliefern, falls vorhanden
if config.FRONTEND_DIST.is_dir():
    app.mount(
        '/', _SpaStaticFiles(directory=config.FRONTEND_DIST, html=True), name='frontend'
    )
