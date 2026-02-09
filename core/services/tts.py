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
    "moonlight_elder": {"language_code": "en-ZA", "name": "", "rate": 0.82, "pitch": -5.5, "gender": "MALE"},
    "village_fire": {"language_code": "en-ZA", "name": "", "rate": 0.93, "pitch": -2.0, "gender": "MALE"},
    "wise_judge": {"language_code": "en-ZA", "name": "", "rate": 0.86, "pitch": -4.0, "gender": "MALE"},
    "hopeful_healer": {"language_code": "en-ZA", "name": "", "rate": 0.88, "pitch": -3.0, "gender": "MALE"},
    "playful_trickster": {"language_code": "en-ZA", "name": "", "rate": 0.96, "pitch": -1.0, "gender": "MALE"},
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


def _escape_ssml(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _env_float(name, default):
    raw = os.environ.get(name, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _cadence_ssml(text, tone_key):
    escaped = _escape_ssml((text or "").strip())
    if not escaped:
        return ""

    sentence_parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", escaped) if s.strip()]
    if not sentence_parts:
        sentence_parts = [escaped]

    pause_scale = _env_float("TTS_PAUSE_SCALE", 0.72)
    life_scale = _env_float("TTS_LIFE_SCALE", 1.18)
    speed_scale = _env_float("TTS_SPEED_SCALE", 1.10)
    use_ellipsis_bridge = _env_bool("TTS_USE_ELLIPSIS_BRIDGE", True)
    use_hum_bridge = _env_bool("TTS_USE_HUM_BRIDGE", True)
    hum_every = max(2, int(_env_float("TTS_HUM_EVERY", 4)))

    if tone_key == "moonlight_elder":
        sentence_break_ms = 360
        line_rate = 88
        line_pitch = -2.5
    elif tone_key == "wise_judge":
        sentence_break_ms = 300
        line_rate = 92
        line_pitch = -1.5
    elif tone_key == "village_fire":
        sentence_break_ms = 240
        line_rate = 97
        line_pitch = -0.5
    elif tone_key == "hopeful_healer":
        sentence_break_ms = 270
        line_rate = 93
        line_pitch = -0.8
    else:
        sentence_break_ms = 220
        line_rate = 99
        line_pitch = 0.0

    line_rate = min(max(int(line_rate * speed_scale), 84), 118)
    sentence_break_ms = max(110, int((sentence_break_ms * pause_scale) / max(speed_scale, 0.7)))

    def _ms(value):
        return f"{max(45, int((value * pause_scale) / max(speed_scale, 0.7)))}ms"

    shaped = []
    for idx, line in enumerate(sentence_parts):
        # add short rhythmic pause around commas/semicolons for oral cadence
        line = re.sub(r"\s*,\s*", f' <break time="{_ms(110)}"/> ', line)
        line = re.sub(r"\s*;\s*", f' <break time="{_ms(135)}"/> ', line)
        line = re.sub(r"\s*:\s*", f' <break time="{_ms(120)}"/> ', line)
        line = re.sub(r"\b(Moral|Themes|Best fit proverb)\b", r"<emphasis level=\"moderate\">\1</emphasis>", line)
        # Slight per-line variation adds life while staying natural.
        dynamic_rate = line_rate
        dynamic_pitch = line_pitch
        if idx % 3 == 1:
            dynamic_rate = max(int(line_rate - (2 * life_scale)), 82)
            dynamic_pitch = line_pitch - (0.2 * life_scale)
        elif idx % 3 == 2:
            dynamic_rate = min(int(line_rate + (2 * life_scale)), 104)
            dynamic_pitch = line_pitch + (0.2 * life_scale)

        # Give quoted dialogue a subtle emphasis so it sounds more performed.
        line = re.sub(
            r"&quot;([^&]+)&quot;",
            r'<emphasis level="reduced">"\1"</emphasis>',
            line,
        )
        shaped.append(
            f'<prosody rate="{dynamic_rate}%" pitch="{dynamic_pitch:.1f}st" volume="medium">{line}</prosody>'
        )

    segments = []
    for idx, line in enumerate(shaped):
        segments.append(line)
        if idx >= len(shaped) - 1:
            continue
        bridge = [f'<break time="{_ms(55)}"/>']
        if use_ellipsis_bridge:
            bridge.append('<prosody rate="84%" volume="x-soft">...</prosody>')
        if use_hum_bridge and (idx + 1) % hum_every == 0:
            bridge.append('<prosody rate="80%" pitch="-3.4st" volume="soft">hmm...</prosody>')
        bridge.append(f'<break time="{sentence_break_ms}ms"/>')
        segments.append("".join(bridge))

    body = "".join(segments)
    return (
        f'<speak><break time="{_ms(160)}"/><p>'
        + body
        + f'</p><break time="{_ms(170)}"/></speak>'
    )


def _synthesize_via_rest(creds, script, ssml_script, voice, use_ssml=True):
    if not creds.valid or creds.expired or not creds.token:
        creds.refresh(Request())

    payload = {
        "input": {"ssml": ssml_script} if (use_ssml and ssml_script) else {"text": script},
        "voice": {
            "languageCode": voice["language_code"],
            "ssmlGender": voice.get("gender", "MALE"),
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
    if response.status_code >= 400:
        raise RuntimeError(
            f"Google REST TTS error {response.status_code}: {response.text[:1200]}"
        )
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
    voice = dict(_VOICE_MAP.get(tone_key, _VOICE_MAP["moonlight_elder"]))
    speed_scale = _env_float("TTS_SPEED_SCALE", 1.10)
    voice["rate"] = round(min(max(voice["rate"] * speed_scale, 0.7), 1.6), 2)
    gender_override = os.environ.get("TTS_VOICE_GENDER", voice.get("gender", "MALE")).strip().upper()
    if gender_override in {"MALE", "FEMALE", "NEUTRAL"}:
        voice["gender"] = gender_override

    # Build cadenced SSML automatically for stronger oral-story rhythm.
    if not ssml_script and script:
        ssml_script = _cadence_ssml(script, tone_key)

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
    gender_map = {
        "MALE": texttospeech.SsmlVoiceGender.MALE,
        "FEMALE": texttospeech.SsmlVoiceGender.FEMALE,
        "NEUTRAL": texttospeech.SsmlVoiceGender.NEUTRAL,
    }
    voice_params_kwargs["ssml_gender"] = gender_map.get(voice.get("gender", "MALE"), texttospeech.SsmlVoiceGender.MALE)
    voice_params = texttospeech.VoiceSelectionParams(**voice_params_kwargs)
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=voice["rate"],
        pitch=voice["pitch"],
    )

    audio_content = None
    errors = []
    try:
        response = client.synthesize_speech(
            request={
                "input": synthesis_input,
                "voice": voice_params,
                "audio_config": audio_config,
            }
        )
        audio_content = response.audio_content
    except Exception as exc:
        errors.append(f"gRPC synthesize failed: {exc}")

    rest_voice = {
        "language_code": selected_language,
        "name": selected_voice,
        "rate": voice["rate"],
        "pitch": voice["pitch"],
        "gender": voice.get("gender", "MALE"),
    }
    if not audio_content:
        try:
            audio_content = _synthesize_via_rest(
                creds, script, ssml_script, rest_voice, use_ssml=True
            )
        except Exception as exc:
            errors.append(str(exc))

    # Fallback: plain text, relaxed voice settings.
    if not audio_content:
        for lang in (selected_language, "en-ZA", "en-GB", "en-US"):
            try:
                relaxed_voice = dict(rest_voice)
                relaxed_voice["language_code"] = lang
                relaxed_voice["name"] = ""
                audio_content = _synthesize_via_rest(
                    creds, script, ssml_script="", voice=relaxed_voice, use_ssml=False
                )
                if audio_content:
                    selected_language = lang
                    selected_voice = ""
                    break
            except Exception as exc:
                errors.append(str(exc))

    if not audio_content:
        raise RuntimeError("Google TTS failed after fallbacks. " + " | ".join(errors[-4:]))

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
