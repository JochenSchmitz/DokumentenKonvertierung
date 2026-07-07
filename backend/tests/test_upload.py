"""Upload-Tests: Duplikat-Erkennung (SHA-256) und ZIP-Entpacken.

Nutzt die Test-DB aus conftest.py; die Original-Dateien landen im
echten data/originals und werden am Testende per DELETE wieder
entfernt. Die Inhalte sind pro Lauf zufällig, damit Reste eines
abgebrochenen Laufs keine falschen Duplikat-Treffer erzeugen.
"""

import io
import os
import zipfile

from fastapi.testclient import TestClient

from backend.app.main import app


def _login(client: TestClient) -> None:
    resp = client.post(
        '/api/auth/login',
        json={'email': 'test@example.org', 'password': 'test-passwort'},
    )
    assert resp.status_code == 200


def test_duplikat_wird_abgelehnt():
    with TestClient(app) as client:
        _login(client)
        payload = b'%PDF-testinhalt-' + os.urandom(16).hex().encode()

        resp = client.post(
            '/api/documents',
            files=[('files', ('bericht.pdf', payload, 'application/pdf'))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data['created']) == 1
        assert data['skipped'] == []
        doc_id = data['created'][0]['id']

        try:
            # Gleicher Inhalt, anderer Name -> Duplikat, kurzer Hinweis
            resp = client.post(
                '/api/documents',
                files=[('files', ('kopie.pdf', payload, 'application/pdf'))],
            )
            data = resp.json()
            assert data['created'] == []
            assert len(data['skipped']) == 1
            assert 'bereits vorhanden' in data['skipped'][0]['reason']
            assert 'bericht.pdf' in data['skipped'][0]['reason']
        finally:
            assert client.delete(f'/api/documents/{doc_id}').status_code == 204


def test_zip_wird_entpackt_ordner_werden_tags():
    with TestClient(app) as client:
        _login(client)
        pdf = b'%PDF-zipinhalt-' + os.urandom(16).hex().encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('Projekt A/Berichte/scan.pdf', pdf)
            zf.writestr('Projekt A/notiz.txt', b'kein unterstuetzter Typ')
            zf.writestr('Projekt A/Berichte/nochmal.pdf', pdf)  # Duplikat im ZIP

        resp = client.post(
            '/api/documents',
            files=[('files', ('archiv.zip', buf.getvalue(), 'application/zip'))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data['created']) == 1
        doc = data['created'][0]
        try:
            assert doc['filename'] == 'scan.pdf'
            # Ordnerstruktur im Archiv -> Schlagworte, sofort sichtbar
            assert doc['tags'] == ['Projekt A', 'Berichte']

            reasons = {s['filename']: s['reason'] for s in data['skipped']}
            assert 'nicht unterstützt' in reasons['Projekt A/notiz.txt']
            assert (
                'mehrfach im selben Upload' in reasons['Projekt A/Berichte/nochmal.pdf']
            )
        finally:
            assert client.delete(f'/api/documents/{doc["id"]}').status_code == 204


def test_unbekannter_typ_wird_abgelehnt():
    with TestClient(app) as client:
        _login(client)
        resp = client.post(
            '/api/documents',
            files=[('files', ('tabelle.xlsx', b'x', 'application/octet-stream'))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['created'] == []
        assert 'nicht unterstützt' in data['skipped'][0]['reason']
