"""Smoke-Test: App startet, Anmeldung funktioniert, Endpunkte geschützt.

Voraussetzung: die Postgres aus docker-compose läuft (Port 5435).
"""

import os

os.environ.setdefault('AUTH_USERS', 'test@example.org:test-passwort')
os.environ.setdefault('AUTH_SECRET', 'test-secret')
# Kein Worker in Tests — er würde echte Dokumente aus der Queue claimen
os.environ['WORKER_ENABLED'] = '0'

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402


def test_ohne_anmeldung_401():
    with TestClient(app) as client:
        assert client.get('/api/config').status_code == 401
        assert client.get('/api/documents').status_code == 401


def test_login_und_zugriff():
    with TestClient(app) as client:
        resp = client.post(
            '/api/auth/login',
            json={'email': 'falsch@example.org', 'password': 'x'},
        )
        assert resp.status_code == 401

        resp = client.post(
            '/api/auth/login',
            json={'email': 'test@example.org', 'password': 'test-passwort'},
        )
        assert resp.status_code == 200
        assert 'session' in resp.cookies

        # Sitzung gilt 7 Tage
        assert 'Max-Age=604800' in resp.headers['set-cookie']

        assert client.get('/api/config').status_code == 200
        resp = client.get('/api/documents')
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        assert client.get('/api/auth/me').json()['email'] == 'test@example.org'
