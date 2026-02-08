import base64
import logging
import os
import re
from pathlib import Path

import requests
from google.cloud import texttospeech
from google.auth.transport.requests import Request
from google.oauth2 import service_account


_VOICE_MAP = {
    # Prefer South African English locale for a more African-accent baseline.
    # Name is optional and can be overridden with env vars if you have preferred voices.
    "moonlight_elder": {"language_code": "en-ZA", "name": "", "rate": 0.84, "pitch": -4.0},
    "village_fire": {"language_code": "en-ZA", "name": "", "rate": 0.95, "pitch": -1.0},
    "wise_judge": {"language_code": "en-ZA", "name": "", "rate": 0.88, "pitch": -2.0},
    "hopeful_healer": {"language_code": "en-ZA", "name": "", "rate": 0.9, "pitch": 0.0},
    "playful_trickster": {"language_code": "en-ZA", "name": "", "rate": 0.98, "pitch": 1.5},
}

_FALLBACK_LANGS = ("en-NG", "en-ZA", "en-GB")
_VOICE_CACHE = {}


def _credentials_path():
    default_path = Path(__file__).resolve().parent.parent / "vertex-sa.json"
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path:
        env_path = Path(creds_path)
        if env_path.exists():
            return env_path
    return default_path


def _make_credentials():
    creds_path = _credentials_path()
    if not creds_path.exists():
        raise RuntimeError(
            "Google TTS credentials not found. Set GOOGLE_APPLICATION_CREDENTIALS "
            "or provide core/vertex-sa.json."
        )
    return service_account.Credentials.from_service_account_file(
        str(creds_path),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _make_client(creds):
    return texttospeech.TextToSpeechClient(credentials=creds)


def _voice_candidates_for_language(voices, language_code):
    candidates = []
    for voice in voices:
        if language_code not in (voice.language_codes or []):
            continue
        name = voice.name or ""
        if "Neural2" in name:
            priority = 0
        elif "Wavenet" in name or "WaveNet" in name:
            priority = 1
        elif "Studio" in name:
            priority = 2
        elif "Standard" in name:
            priority = 3
        else:
            priority = 4
        candidates.append((priority, name))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [name for _, name in candidates]


def _resolve_voice_selection(client, requested_language, requested_voice):
    logger = logging.getLogger(__name__)
    cache_key = (requested_language, requested_voice or "")
    if cache_key in _VOICE_CACHE:
        return _VOICE_CACHE[cache_key]

    selected_language = requested_language
    selected_voice = requested_voice
    try:
        voices = client.list_voices().voices
    except Exception:
        # If voices listing is unavailable, use requested values directly.
        _VOICE_CACHE[cache_key] = (selected_language, selected_voice)
        return selected_language, selected_voice

    by_language = {
        lang: _voice_candidates_for_language(voices, lang)
        for lang in _FALLBACK_LANGS
    }
    if requested_language not in by_language:
        by_language[requested_language] = _voice_candidates_for_language(voices, requested_language)

    if requested_voice:
        has_requested_voice = any((v.name == requested_voice) for v in voices)
        if has_requested_voice:
            _VOICE_CACHE[cache_key] = (selected_language, selected_voice)
            return selected_language, selected_voice
        logger.warning("Requested TTS voice %s not found. Falling back.", requested_voice)
        selected_voice = ""

    language_order = [requested_language] + [lang for lang in _FALLBACK_LANGS if lang != requested_language]
    for lang in language_order:
        names = by_language.get(lang) or []
        if names:
            selected_language = lang
            selected_voice = names[0]
            break

    _VOICE_CACHE[cache_key] = (selected_language, selected_voice)
    return selected_language, selected_voice


def _synthesize_via_rest(creds, script, ssml_script, voice):
    if not creds.valid or creds.expired or not creds.token:
        creds.refresh(Request())

    payload = {
        "input": {"ssml": ssml_script} if ssml_script else {"text": script},
        "voice": {
            "languageCode": voice["language_code"],
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": voice["rate"],
            "pitch": voice["pitch"],
        },
    }
    if voice.get("name"):
        payload["voice"]["name"] = voice["name"]
    response = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={"Authorization": f"Bearer {creds.token}"},
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    audio_b64 = data.get("audioContent")
    if not audio_b64:
        return None
    return base64.b64decode(audio_b64)


def synthesize(text, ssml=None, tone=None):
    logger = logging.getLogger(__name__)
    script = (text or "").strip()
    ssml_script = (ssml or "").strip()
    if not script and not ssml_script:
        return None

    creds = _make_credentials()
    tone_key = (tone or "moonlight_elder").strip()
    voice = _VOICE_MAP.get(tone_key, _VOICE_MAP["moonlight_elder"])
    requested_language = os.environ.get("TTS_LANGUAGE_CODE", voice["language_code"]).strip() or voice["language_code"]
    requested_voice = os.environ.get("TTS_VOICE_NAME", voice.get("name", "")).strip()

    client = _make_client(creds)
    selected_language, selected_voice = _resolve_voice_selection(
        client=client,
        requested_language=requested_language,
        requested_voice=requested_voice,
    )

    synthesis_input = (
        texttospeech.SynthesisInput(ssml=ssml_script)
        if ssml_script
        else texttospeech.SynthesisInput(text=script)
    )
    voice_params_kwargs = {"language_code": selected_language}
    if selected_voice:
        voice_params_kwargs["name"] = selected_voice
    voice_params = texttospeech.VoiceSelectionParams(**voice_params_kwargs)
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=voice["rate"],
        pitch=voice["pitch"],
    )

    audio_content = None
    try:
        response = client.synthesize_speech(
            request={
                "input": synthesis_input,
                "voice": voice_params,
                "audio_config": audio_config,
            }
        )
        audio_content = response.audio_content
    except Exception:
        rest_voice = {
            "language_code": selected_language,
            "name": selected_voice,
            "rate": voice["rate"],
            "pitch": voice["pitch"],
        }
        audio_content = _synthesize_via_rest(creds, script, ssml_script, rest_voice)

    if not audio_content:
        return None

    logger.info(
        "Google TTS voice selected: language=%s voice=%s tone=%s",
        selected_language,
        selected_voice or "<auto>",
        tone_key,
    )

    subtitle_text = script or _strip_ssml(ssml_script)
    subtitles = _build_subtitles(subtitle_text)
    return {
        "audio_data": base64.b64encode(audio_content).decode("ascii"),
        "audio_format": "audio/mpeg",
        "subtitles": subtitles,
    }


def _strip_ssml(ssml):
    return re.sub(r"<[^>]+>", "", ssml)


def _build_subtitles(text):
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    subtitles = []
    time_cursor = 0.0
    words_per_second = 2.4

    for sentence in sentences:
        word_count = max(len(sentence.split()), 1)
        duration = word_count / words_per_second
        subtitles.append(
            {
                "start_sec": round(time_cursor, 2),
                "end_sec": round(time_cursor + duration, 2),
                "text": sentence,
            }
        )
        time_cursor += duration

    return subtitles
