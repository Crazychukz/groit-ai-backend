import json
import os
import random
import time

import requests


class GeminiError(Exception):
    pass


def _request(payload):
    api_key = os.environ.get('GEMINI_API_KEY', "AIzaSyCbx9j9L6Zj4fyd5r-YXAP1fz7roO1E3iI")
    if not api_key:
        raise GeminiError('GEMINI_API_KEY is not set')

    model = os.environ.get('GEMINI_MODEL', 'gemini-3-flash-preview')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    retries = int(os.environ.get('GEMINI_TEXT_RETRIES', '4'))
    base_delay = float(os.environ.get('GEMINI_TEXT_RETRY_BASE_DELAY_SEC', '0.8'))
    timeout_sec = float(os.environ.get('GEMINI_TEXT_TIMEOUT_SEC', '90'))
    retry_statuses = {408, 409, 425, 429, 500, 502, 503, 504}

    last_error = None
    data = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                url,
                params={'key': api_key},
                json=payload,
                timeout=timeout_sec,
            )
            if response.status_code >= 400:
                if response.status_code in retry_statuses and attempt < retries:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.25)
                    time.sleep(delay)
                    continue
                raise GeminiError(f"Gemini error {response.status_code}: {response.text}")
            data = response.json()
            break
        except requests.exceptions.Timeout as exc:
            last_error = exc
            if attempt >= retries:
                raise GeminiError(
                    f"Gemini request timed out after retries (timeout={timeout_sec}s)."
                ) from exc
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.25)
            time.sleep(delay)
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                raise GeminiError(f"Gemini request failed after retries: {exc}") from exc
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.25)
            time.sleep(delay)

    if data is None:
        raise GeminiError(f"Gemini request failed after retries: {last_error}")

    candidates = data.get('candidates') or []
    if not candidates:
        raise GeminiError('Gemini returned no candidates')
    parts = candidates[0].get('content', {}).get('parts', [])
    if not parts:
        raise GeminiError('Gemini returned empty content')
    return parts[0].get('text', '')


def generate_json(system_prompt, user_prompt):
    payload = {
        'systemInstruction': {
            'parts': [{'text': system_prompt}]
        },
        'contents': [
            {
                'role': 'user',
                'parts': [{'text': user_prompt}]
            }
        ],
        'generationConfig': {
            'temperature': 0.3,
            'responseMimeType': 'application/json'
        }
    }
    text = _request(payload)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Invalid JSON from Gemini: {exc}")


def generate_text(system_prompt, user_prompt):
    payload = {
        'systemInstruction': {
            'parts': [{'text': system_prompt}]
        },
        'contents': [
            {
                'role': 'user',
                'parts': [{'text': user_prompt}]
            }
        ],
        'generationConfig': {
            'temperature': 0.6
        }
    }
    return _request(payload)


def list_models():
    api_key = os.environ.get('GEMINI_API_KEY', "AIzaSyCbx9j9L6Zj4fyd5r-YXAP1fz7roO1E3iI")
    if not api_key:
        raise GeminiError('GEMINI_API_KEY is not set')

    url = 'https://generativelanguage.googleapis.com/v1beta/models'
    response = requests.get(url, params={'key': api_key}, timeout=30)
    if response.status_code >= 400:
        raise GeminiError(f"Gemini error {response.status_code}: {response.text}")
    data = response.json()
    return data.get('models', [])
