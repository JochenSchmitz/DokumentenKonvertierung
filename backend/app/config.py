"""Zentrale Konfiguration — alles per Umgebungsvariable übersteuerbar."""

import os
import secrets
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]

# Server-IP, unter der Browser UND OnlyOffice-Container uns erreichen
PUBLIC_HOST = os.environ.get('PUBLIC_HOST', '172.31.102.13')
BACKEND_PORT = int(os.environ.get('BACKEND_PORT', '8020'))
PUBLIC_BASE_URL = os.environ.get(
    'PUBLIC_BASE_URL', f'http://{PUBLIC_HOST}:{BACKEND_PORT}'
)

DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql+psycopg://dokumente:dokumente-dev@localhost:5435/dokumente',
)

VLLM_URL = os.environ.get('VLLM_URL', 'http://localhost:8012/v1')
VLLM_MODEL = os.environ.get('VLLM_MODEL', 'Qwen/Qwen3-VL-32B-Instruct-FP8')
# Reale Modell-Endpunkte (kommagetrennt) für Health/Token-Metriken. Die
# Inferenz läuft über VLLM_URL (im Mehr-GPU-Betrieb der haproxy-LB); für
# Verfügbarkeit und Token-Zähler fragen wir dagegen die echten vLLMs
# einzeln ab und summieren. Default: der eine VLLM_URL.
VLLM_ENDPOINTS = [
    e.strip()
    for e in os.environ.get('VLLM_ENDPOINTS', VLLM_URL).split(',')
    if e.strip()
]

ONLYOFFICE_URL = os.environ.get('ONLYOFFICE_URL', f'http://{PUBLIC_HOST}:8082')
ONLYOFFICE_JWT_SECRET = os.environ.get(
    'ONLYOFFICE_JWT_SECRET', 'dev-onlyoffice-secret-change-me-32-bytes'
)

# --- Anmeldung: Benutzer aus AUTH_USERS ("email:pw,email:pw"), Sitzung
# als signierter Cookie. Ohne AUTH_SECRET in der .env gelten Sitzungen
# nur bis zum nächsten Neustart (zufälliger Schlüssel).
AUTH_SECRET = os.environ.get('AUTH_SECRET') or secrets.token_hex(32)
AUTH_SESSION_DAYS = int(os.environ.get('AUTH_SESSION_DAYS', '7'))
AUTH_USERS: dict[str, str] = {}
for _pair in os.environ.get('AUTH_USERS', '').split(','):
    if ':' in _pair:
        _email, _pw = _pair.split(':', 1)
        AUTH_USERS[_email.strip().lower()] = _pw.strip()

ORIGINALS_DIR = PROJECT_DIR / 'data' / 'originals'
RESULTS_DIR = PROJECT_DIR / 'ergebnisse'
FRONTEND_DIST = PROJECT_DIR / 'frontend' / 'dist'

OCR_PARALLEL = int(os.environ.get('OCR_PARALLEL', '4'))
OCR_DPI = int(os.environ.get('OCR_DPI', '200'))
# Wie viele Dokumente der Entitäten-Nachlauf gleichzeitig ans Modell gibt.
# Die Extraktion ist reiner Text (bis 40k Zeichen); 8 gleichzeitig belegen
# nur einen Bruchteil des KV-Caches und bringen ggü. seriell ~5x Durchsatz.
ENTITY_PARALLEL = int(os.environ.get('ENTITY_PARALLEL', '8'))
# Wie viele Dokumente gleichzeitig verarbeitet werden (Dokument-Ebene). Alle
# teilen sich das globale OCR_PARALLEL-Seitenbudget. Bei einer GPU reicht 1;
# mit dem Mehr-GPU-LB höher, damit auch viele kleine Dokumente beide GPUs
# auslasten (ein einzelnes kleines Dokument allein tut das nicht).
DOC_PARALLEL = int(os.environ.get('DOC_PARALLEL', '1'))

OCR_PROMPT = (
    'Extrahiere den vollständigen Text dieser gescannten Dokumentseite. '
    'Gib den Inhalt als sauberes Markdown wieder: Überschriften als '
    'Überschriften, Tabellen als Markdown-Tabellen, Listen als Listen. '
    'Gib ausschließlich den Dokumentinhalt aus, keine Kommentare. '
    'Unleserliche Stellen markiere mit [unleserlich]. '
    'Logos und Grafiken nicht als Bild-Links einbetten, sondern kurz in '
    'eckigen Klammern beschreiben, z.B. [Logo: SwS].\n'
    'Regeln für Tabellen:\n'
    '- Jede Tabelle als GitHub-Markdown-Pipe-Tabelle: Kopfzeile, dann '
    'Trennzeile (|---|), dann genau eine Markdown-Zeile pro Tabellenzeile.\n'
    '- Hat die Tabelle im Original keine Kopfzeile (z.B. ein Formular mit '
    'Beschriftung links und Inhalt rechts), verwende die Kopfzeile '
    '| Feld | Inhalt |.\n'
    '- Mehrere Absätze oder Aufzählungspunkte innerhalb EINER Zelle mit '
    '<br> trennen, niemals mit echten Zeilenumbrüchen.\n'
    '- Verbundene Zellen: Wert in der ersten Zelle, restliche Zellen leer.\n'
    '- Senkrechte Striche im Zellinhalt als \\| maskieren.\n'
    '- Text, der keine Tabelle ist, nicht in Tabellen pressen.'
)

# Für hochgeladene Bilddateien: gescannte/fotografierte Dokumente werden
# wie gehabt als Text extrahiert, "richtige" Fotos dagegen so genau wie
# möglich beschrieben.
IMAGE_PROMPT = (
    'Du siehst eine hochgeladene Bilddatei.\n'
    'Fall 1 — die Datei zeigt eine gescannte oder abfotografierte '
    'Dokumentseite: ' + OCR_PROMPT + '\n'
    'Fall 2 — die Datei ist ein richtiges Foto oder Bild ohne '
    'Dokumentcharakter: Beschreibe das Bild auf Deutsch so genau und '
    'detailliert wie möglich als Markdown. Beginne mit einer kurzen '
    'Überschrift, dann eine ausführliche Beschreibung: Was ist zu sehen '
    '(Personen, Objekte, Gebäude, Fahrzeuge, Umgebung), räumliche '
    'Anordnung, Farben, Lichtverhältnisse, erkennbarer Text (Schilder, '
    'Aufschriften, Kennzeichen), Zustand/Auffälligkeiten sowie Hinweise '
    'auf Ort, Zeit oder Anlass der Aufnahme. Spekulationen als solche '
    'kennzeichnen.'
)

METADATA_PROMPT = (
    'Du bekommst den extrahierten Text eines gescannten Dokuments. '
    'Erstelle Metadaten dafür und antworte AUSSCHLIESSLICH mit einem '
    'JSON-Objekt in genau dieser Form, ohne Markdown-Codeblock:\n'
    '{"tags": ["5 bis 10 deutsche Schlagworte"], '
    '"summary": "Zusammenfassung in 2-3 Sätzen", '
    '"doc_date": "JJJJ-MM-TT oder null, falls kein Dokumentdatum erkennbar"}\n\n'
    'Dokumenttext:\n'
)

ENTITY_PROMPT = (
    'Du bekommst den extrahierten Text eines Dokuments. Dokumente gehen '
    'oft von einem Absender an einen Empfänger; zusätzlich können weitere '
    'Personen oder Firmen erwähnt werden. Extrahiere ALLE genannten '
    'Personen und Firmen mit ihren Kontaktdaten, so vollständig wie der '
    'Text es hergibt. Erfinde nichts; fehlende Angaben lässt du weg (null). '
    'Antworte AUSSCHLIESSLICH mit einem JSON-Array in genau dieser Form, '
    'ohne Markdown-Codeblock:\n'
    '[{"role": "absender|empfaenger|erwaehnt", '
    '"kind": "person|firma", '
    '"name": "Vor- und Nachname der Person oder null", '
    '"company": "Firmenname oder null", '
    '"address": "vollständige Anschrift in einer Zeile oder null", '
    '"phone": "Telefonnummer oder null", '
    '"email": "E-Mail-Adresse oder null"}]\n'
    'Regeln: Absender = von wem das Dokument stammt (Briefkopf, '
    'Unterschrift); Empfänger = an wen es gerichtet ist (Adressfeld). Ist '
    'die Rolle nicht erkennbar, verwende "erwaehnt". Eine Firma ohne '
    'konkrete Ansprechperson: "name" null, "kind" "firma". Gehört eine '
    'Person zu einer Firma, fülle beide Felder. Gib ein leeres Array [] '
    'zurück, wenn niemand genannt ist.\n\n'
    'Dokumenttext:\n'
)
