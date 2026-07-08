"""Anbindung an das Vision-Modell (vLLM, OpenAI-kompatible API)."""

import base64
import datetime
import json
import logging
import re

import httpx

from . import config

log = logging.getLogger('ocr')


async def _chat(
    client: httpx.AsyncClient, content: list | str, max_tokens: int = 8000
) -> str:
    # Grosszügiger Timeout: bei hoher Parallelität sinkt die Rate pro
    # Anfrage; eine tabellenlastige Seite kann > 30 min brauchen.
    resp = await client.post(
        f'{config.VLLM_URL}/chat/completions',
        json={
            'model': config.VLLM_MODEL,
            'messages': [{'role': 'user', 'content': content}],
            'max_tokens': max_tokens,
            'temperature': 0,
        },
        timeout=5400,
    )
    if resp.status_code >= 400:
        # Der Body nennt den echten Grund (z.B. Kontextfenster gesprengt)
        log.warning('vLLM %d: %s', resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


async def ocr_page(
    client: httpx.AsyncClient, png_bytes: bytes, prompt: str | None = None
) -> str:
    b64 = base64.b64encode(png_bytes).decode()
    return await _chat(
        client,
        [
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
            {'type': 'text', 'text': prompt or config.OCR_PROMPT},
        ],
    )


async def extract_metadata(client: httpx.AsyncClient, full_text: str) -> dict:
    """Schlagworte, Zusammenfassung und Dokumentdatum aus dem Text ableiten.

    Die Zeichen-Obergrenze ist nur eine Heuristik: zahlenlastige
    Tabellen (Leistungsverzeichnisse!) tokenisieren mit ~1,1 Token pro
    Zeichen statt ~0,4 und sprengen dann das Kontextfenster (HTTP 400,
    64k seit --max-model-len 65536). In dem Fall wird der Text
    schrittweise gekürzt.
    """
    raw = ''
    for cap in (40000, 15000, 4000):
        try:
            raw = await _chat(
                client, config.METADATA_PROMPT + full_text[:cap], max_tokens=1000
            )
            break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and cap > 4000:
                log.warning(
                    'Metadaten-Prompt zu lang (%d Zeichen) — kürze und versuche erneut',
                    cap,
                )
                continue
            raise
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    meta: dict = {'tags': [], 'summary': None, 'doc_date': None}
    if not match:
        return meta
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return meta
    tags = data.get('tags')
    if isinstance(tags, list):
        meta['tags'] = [str(t).strip() for t in tags if str(t).strip()][:10]
    if isinstance(data.get('summary'), str):
        meta['summary'] = data['summary'].strip()
    raw_date = data.get('doc_date')
    if isinstance(raw_date, str):
        try:
            meta['doc_date'] = datetime.date.fromisoformat(raw_date.strip())
        except ValueError:
            pass
    return meta


_ROLE_MAP = {
    'absender': 'sender', 'sender': 'sender', 'von': 'sender',
    'empfaenger': 'recipient', 'empfänger': 'recipient',
    'recipient': 'recipient', 'an': 'recipient',
    'erwaehnt': 'mentioned', 'erwähnt': 'mentioned', 'mentioned': 'mentioned',
}  # fmt: skip
_KIND_MAP = {
    'person': 'person',
    'firma': 'organization', 'organization': 'organization',
    'organisation': 'organization', 'unternehmen': 'organization',
}  # fmt: skip


async def extract_entities(client: httpx.AsyncClient, full_text: str) -> list[dict]:
    """Genannte Personen/Firmen mit Kontaktdaten aus dem Text ziehen.

    Best effort: das Modell liefert ein JSON-Array; nicht Parsebares oder
    komplett leere Einträge werden verworfen. Rollen-/Art-Strings werden
    auf die DB-Enums abgebildet (unbekannt -> 'mentioned' bzw. kind=None).
    Wie bei den Metadaten wird der Text bei zu langem Kontext gekürzt.
    """
    raw = ''
    for cap in (40000, 15000, 4000):
        try:
            raw = await _chat(
                client, config.ENTITY_PROMPT + full_text[:cap], max_tokens=2000
            )
            break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and cap > 4000:
                log.warning(
                    'Entitäten-Prompt zu lang (%d Zeichen) — kürze und erneut', cap
                )
                continue
            raise
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        def field(key: str, src: dict = item) -> str | None:
            val = src.get(key)
            text = str(val).strip() if val is not None else ''
            return text or None

        name = field('name')
        company = field('company')
        address = field('address')
        phone = field('phone')
        email = field('email')
        # Einträge ohne jede Angabe sind wertlos
        if not any((name, company, address, phone, email)):
            continue
        role = _ROLE_MAP.get(str(item.get('role', '')).strip().lower(), 'mentioned')
        kind = _KIND_MAP.get(str(item.get('kind', '')).strip().lower())
        out.append(
            {
                'role': role,
                'kind': kind,
                'name': name,
                'company': company,
                'address': address,
                'phone': phone,
                'email': email,
            }
        )
    return out


async def is_model_up() -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                config.VLLM_URL.removesuffix('/v1') + '/health', timeout=5
            )
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
