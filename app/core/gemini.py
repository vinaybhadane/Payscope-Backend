"""Bounded retries for transient provider failures; credentials never enter errors."""
import random
import time
import requests


def post_generation(url, *, headers, json, timeout=(10, 90)):
    deadline = time.monotonic() + 180
    response = None
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining < 2:
            break
        try:
            response = requests.post(url, headers=headers, json=json,
                timeout=(min(timeout[0], max(1, remaining / 4)), min(timeout[1], max(1, remaining * .75))))
            if response.status_code not in (408, 429, 500, 502, 503, 504):
                return response
        except requests.RequestException:
            response = None
        if attempt < 2:
            time.sleep(min(2 ** attempt + random.uniform(0, .25), max(0, deadline - time.monotonic())))
    if response is not None:
        return response
    raise ValueError('Gemini could not be reached after automatic retries. Your saved records remain available.')
