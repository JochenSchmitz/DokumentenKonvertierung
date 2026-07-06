"""Smoke-Test: App startet, Anmeldung funktioniert, Endpunkte geschützt.

Voraussetzung: die Postgres aus docker-compose läuft (Port 5435).
Umgebung (Test-DB, Test-User, kein Worker) kommt aus conftest.py.
"""

from fastapi.testclient import TestClient

from backend.app.main import app


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


def test_version_oeffentlich():
    with TestClient(app) as client:
        resp = client.get('/api/version')
        assert resp.status_code == 200
        assert resp.json()['version'].count('.') == 2
