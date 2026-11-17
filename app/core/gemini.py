"""Bounded provider retries and compatible model fallback without losing saved data."""
import hashlib
import random
import re
import time
from threading import Lock
import requests
from app.core.config import settings

_unavailable_until = {}
_lock = Lock()


def _daily_quota(response):
    try:
        error = response.json().get('error', {})
        if not isinstance(error, dict):
            return 0
        daily, delay = False, 3600
        for detail in error.get('details', []):
            for violation in detail.get('violations', []):
                if 'perday' in violation.get('quotaId', '').lower():
                    daily = True
            if 'retryDelay' in detail:
                delay = float(str(detail['retryDelay']).rstrip('s'))
        return min(max(delay, 60), 86400) if daily else 0
    except (ValueError, AttributeError, TypeError):
        return 0


def post_generation(url, *, headers, json, timeout=(10, 90)):
    deadline = time.monotonic() + 180
    model_url = '/models/' in url and url.endswith(':generateContent')
    primary = url.rsplit('/models/', 1)[-1].split(':')[0] if model_url else ''
    alternatives = [m.strip() for m in settings.GEMINI_FALLBACK_MODELS.split(',')
                    if re.fullmatch(r'gemini-[A-Za-z0-9._-]+', m.strip())]
    models = list(dict.fromkeys([primary] + alternatives))[:3] if model_url else ['']
    key = hashlib.sha256(headers.get('x-goog-api-key', '').encode()).hexdigest()
    response = None
    for model in models:
        with _lock:
            if _unavailable_until.get((key, model), 0) > time.monotonic():
                continue
        target = url.rsplit('/models/', 1)[0] + '/models/' + model + ':generateContent' if model_url else url
        body = dict(json)
        body['generationConfig'] = dict(json.get('generationConfig', {}))
        if model.startswith('gemini-3') or model.startswith('gemini-4'):
            body['generationConfig'].pop('temperature', None)
        for attempt in range(3):
            remaining = deadline - time.monotonic()
            if remaining < 2:
                break
            try:
                response = requests.post(target, headers=headers, json=body,
                    timeout=(min(timeout[0], max(1, remaining / 4)), min(timeout[1], max(1, remaining * .75))))
                response._payscope_model = model
                if response.status_code == 429:
                    delay = _daily_quota(response)
                    if delay:
                        with _lock:
                            if len(_unavailable_until) >= 32:
                                _unavailable_until.clear()
                            _unavailable_until[(key, model)] = time.monotonic() + delay
                        break
                if response.status_code in (404, 410) and model_url:
                    break
                if response.status_code not in (408, 429, 500, 502, 503, 504):
                    return response
            except requests.RequestException:
                response = None
            if attempt < 2:
                time.sleep(min(2 ** attempt + random.uniform(0, .25), max(0, deadline - time.monotonic())))
    if response is not None:
        return response
    raise ValueError('Gemini is temporarily unavailable or its allowance is exhausted. Your saved records and spending remain available.')
