"""Anbindung an das Vision-Modell (vLLM, OpenAI-kompatible API)."""

import base64
import datetime
import json
import re

import httpx

from . import config


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
    """Schlagworte, Zusammenfassung und Dokumentdatum aus dem Text ableiten."""
    raw = await _chat(
        client, config.METADATA_PROMPT + full_text[:15000], max_tokens=1000
    )
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


async def is_model_up() -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                config.VLLM_URL.removesuffix('/v1') + '/health', timeout=5
            )
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
