# Dokumenten-Konvertierung (OCR-Pipeline mit Weboberfläche)

Komplette Pipeline: Dokumente im Browser hochladen → Texterkennung durch ein
Vision-Modell → Verschlagwortung/Zusammenfassung/Dokumentdatum automatisch →
Ergebnisse als `.md` + `.docx` in `ergebnisse/` → Metadaten in Postgres →
Side-by-Side-Viewer (Original | OCR-Ergebnis) über OnlyOffice.

**Unterstützte Dateitypen:** PDF, Bilder (PNG/JPG/TIFF — gescannte Seiten
werden gelesen, echte Fotos detailliert beschrieben), Word (`.docx` wird
übernommen, `.doc` per LibreOffice konvertiert), Outlook-E-Mails (`.msg`)
sowie ZIP-Archive (Ordnerstruktur wird zu Schlagworten; auch die formal
defekten OneDrive-Sammel-Downloads > 4 GB werden gelesen). Jede Datei bekommt
einen SHA-256 — erneute Uploads gleichen Inhalts werden mit Hinweis
abgelehnt. Nicht lesbare Dokumente erhalten ein Hinweis-`.docx` und das rote
Schlagwort **Unlesbar**.

## Start

```bash
./dev-temux-start.sh   # Entwicklung: Backend (--reload) + Vite + ngrok in tmux
./dev-temux-stop.sh    # ... alles wieder stoppen
./start-app.sh         # Alternative ohne tmux/ngrok: nur lokal auf Port 8020
```

Weboberfläche: lokal **http://localhost:5175** (Vite) bzw. **http://172.31.102.13:8020**
(Backend liefert den letzten `npm run build`-Stand), extern
**https://dokumentenkonvertierung.ngrok.io** (ngrok, Bezahl-Account).

**Anmeldung:** Alle API-Endpunkte und die Oberfläche erfordern einen Login.
Die Benutzer (E-Mail + Passwort) stehen in der `.env` (`AUTH_USERS`);
die Sitzung hält 7 Tage (`AUTH_SESSION_DAYS`, HttpOnly-Cookie, signiert mit
`AUTH_SECRET`). OnlyOffice ruft Dokumente über kurzlebige, dokumentgebundene
signierte Tokens ab. Neue Benutzer: Zeile in `AUTH_USERS` ergänzen und
Backend neu starten.

Alle Zugangsdaten/Tokens liegen in `.env` (nicht im Git; Vorlage:
`.env.example`). Der OnlyOffice-Viewer bekommt seine öffentliche URL zur
Laufzeit über die lokale ngrok-API (Port 4043, in `ngrok.yml` gepinnt —
4040/4041 gehören den Agenten der Projekte wdf/vip).

## Komponenten

| Komponente | Technik | Wo |
|---|---|---|
| Datenbank | Postgres 18.3 (Docker, `tildeai-db`) | Port 5435, User/DB `dokumente` |
| Backend + Worker | FastAPI/Uvicorn, SQLAlchemy, UUID7 | Port 8020, Code in `backend/` |
| Frontend | Vite + Vue 3 + TypeScript + Pinia | `frontend/` (Build wird vom Backend ausgeliefert) |
| Viewer | OnlyOffice Document Server (bestehender Container) | Port 8082, JWT-signierte Configs |
| OCR/Metadaten | Qwen3-VL-32B-FP8 (Compose-Profil `ocr`) | Port 8012 |

Makefile-Targets: `make backend` / `frontend` / `ngrok` / `infra-up` /
`infra-down` / `quality` (ruff + pytest) / `format` / `test` / `install`.
Versionierter Commit-Helfer: `./commit.sh "Nachricht"` (pflegt `VERSION`).
Git-Remote: https://github.com/JochenSchmitz/DokumentenKonvertierung

Frontend-Entwicklung mit Hot-Reload: `cd frontend && npm run dev`
(Port 5175, proxied `/api` zum Backend); danach `npm run build`, damit das
Backend den neuen Stand ausliefert. Originale liegen in `data/originals/`
(Dateiname = UUID), Ergebnisse in `ergebnisse/`, Volltexte seitenweise in
der Tabelle `pages`.

## OCR-Modell

```bash
docker compose --profile ocr up -d      # Qwen3-VL laden (Port 8012, dauert Minuten)
./ocr-test.sh scans/beispiel.pdf        # Einzeltest: PDF, erste Seite
./ocr-test.sh scans/beispiel.pdf 3      # ... Seite 3
.venv/bin/python ocr-batch.py           # ALLE Dokumente aus scans/ -> ergebnisse/*.md + *.docx
docker compose --profile ocr down
docker compose logs -f                  # Logs (Modell-Laden dauert einige Minuten)
```

API: OpenAI-kompatibel (kein API-Key nötig) unter `http://localhost:8012/v1`.
Der Worker prüft vor jeder Verarbeitung, ob das Modell erreichbar ist —
Dokumente bleiben sonst einfach in der Warteschlange.

`--gpu-memory-utilization 0.60` ist bewusst konservativ, weil der
Ollama-Systemdienst dauerhaft ~22 GB des Unified Memory belegt.

## Verzeichnis

- `backend/` — FastAPI-App (Router, Worker, Modelle, Tests)
- `frontend/` — Vue-3-Oberfläche
- `hf-home/` — Hugging-Face-Cache mit den Modellgewichten (~34 GB), wird in den Container gemountet
- `ocr-test.sh` — Einzelseiten-OCR-Test; `ocr-batch.py` — Batch-OCR für alle Scans
- `scans/` — Eingangsordner für Batch-OCR; `ergebnisse/` — Ausgabe (.md + .docx)
- `data/originals/` — hochgeladene Originaldateien (Dateiname = UUID)
- `.venv/` — Python 3.14 (via uv): `make install`
- `docker-compose.yml` — Postgres + OCR-Modell

## Historie

Das Projekt begann als Test-Setup für TildeOpen-30b (daher der
Verzeichnisname und der DB-Containername `tildeai-db`); dieser Test wurde
entfernt und wird ggf. später separat wieder aufgenommen (Stand: Juli 2026,
zuletzt enthalten bis Version 0.1.11 / Commit `5f9bc72`).
