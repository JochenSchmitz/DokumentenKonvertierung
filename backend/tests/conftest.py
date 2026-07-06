"""Test-Setup: eigene Test-Datenbank, kein Worker, feste Test-User.

Läuft VOR dem Import der App (conftest wird zuerst geladen), damit
backend.app.config die Test-Werte aus der Umgebung übernimmt.
"""

import os

import psycopg

ADMIN_URL = os.environ.get(
    'TEST_ADMIN_DB_URL',
    'postgresql://dokumente:dokumente-dev@localhost:5435/dokumente',
)
TEST_DB = 'dokumente_test'

os.environ['WORKER_ENABLED'] = '0'
os.environ['AUTH_USERS'] = 'test@example.org:test-passwort'
os.environ['AUTH_SECRET'] = 'test-secret-mit-ausreichender-laenge-123456'
os.environ['DATABASE_URL'] = (
    f'postgresql+psycopg://dokumente:dokumente-dev@localhost:5435/{TEST_DB}'
)

# Test-Datenbank anlegen, falls sie noch fehlt
with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
    exists = conn.execute(
        'SELECT 1 FROM pg_database WHERE datname = %s', (TEST_DB,)
    ).fetchone()
    if not exists:
        conn.execute(f'CREATE DATABASE {TEST_DB}')
