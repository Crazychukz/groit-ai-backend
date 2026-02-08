import base64
import io
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account


class GeminiTTSError(Exception):
    pass


TONE_STYLE_MAP = {
    "moonlight_elder": "Deep elder African storyteller voice. Slow, wise, warm, and authoritative.",
    "village_fire": "Energetic village storyteller voice. Rhythmic and vivid, but still grounded.",
    "wise_judge": "Mature elder judge voice. Deliberate, firm, and morally clear.",
    "hopeful_healer": "Gentle elder healer voice. Soft, reassuring, and compassionate.",
    "playful_trickster": "Playful elder trickster voice. Lightly teasing, musical, and expressive.",
}


def _has_wav_header(audio_bytes):
    return len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE"


def _has_mp3_header(audio_bytes):
    if len(audio_bytes) >= 3 and audio_bytes[:3] == b"ID3":
        return True
    if len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0:
        return True
    return False


def _has_ogg_header(audio_bytes):
    return len(audio_bytes) >= 4 and audio_bytes[:4] == b"OggS"


def _has_flac_header(audio_bytes):
    return len(audio_bytes) >= 4 and audio_bytes[:4] == b"fLaC"


def _pcm_to_wav(pcm_bytes, sample_rate, channels):
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)  # LINEAR16 = signed 16-bit PCM
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        return buffer.getvalue()


def _get_param(value, name):
    match = re.search(rf"{name}\s*=\s*(\d+)", value)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _normalize_audio(audio_b64, mime_type):
    audio_bytes = base64.b64decode(audio_b64)
    mime = (mime_type or "").lower()
    is_pcm_mime = "l16" in mime or "linear16" in mime or "codec=pcm" in mime

    # Wrap raw PCM only when MIME explicitly indicates PCM.
    if is_pcm_mime:
        sample_rate = _get_param(mime, "rate") or 24000
        channels = _get_param(mime, "channels") or 1
        wav_bytes = _pcm_to_wav(audio_bytes, sample_rate=sample_rate, channels=channels)
        return base64.b64encode(wav_bytes).decode("ascii"), "audio/wav"

    if _has_wav_header(audio_bytes):
        return audio_b64, "audio/wav"
    if _has_mp3_header(audio_bytes):
        return audio_b64, "audio/mpeg"
    if _has_ogg_header(audio_bytes):
        return audio_b64, "audio/ogg"
    if _has_flac_header(audio_bytes):
        return audio_b64, "audio/flac"
    return audio_b64, mime_type


def _get_access_token():
    default_path = Path(__file__).resolve().parent.parent / "vertex-sa.json"
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and Path(creds_path).exists():
        resolved_path = Path(creds_path)
    else:
        resolved_path = default_path
    if not resolved_path.exists():
        return None
    creds = service_account.Credentials.from_service_account_file(
        str(resolved_path),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(Request())
    return creds.token


def _resolve_tone_style(tone_label):
    tone = (tone_label or "").strip().lower().replace(" ", "_")
    return TONE_STYLE_MAP.get(
        tone,
        "Deep elder African storyteller voice. Slow, wise, warm, and authoritative.",
    )


def _split_story(text, max_words):
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= max_words:
        return [text.strip()]
    chunks = []
    start = 0
    total = len(words)
    while start < total:
        end = min(start + max_words, total)
        chunks.append(" ".join(words[start:end]).strip())
        start = end
    return chunks


def _parse_vertex_response(data):
    if isinstance(data, list):
        dict_item = next((item for item in data if isinstance(item, dict)), None)
        if not dict_item:
            raise GeminiTTSError(
                f"Unexpected Vertex response type list with no object payload: {str(data)[:500]}"
            )
        data = dict_item
    if not isinstance(data, dict):
        raise GeminiTTSError(
            f"Unexpected Vertex response type {type(data).__name__}: {str(data)[:500]}"
        )

    candidates = data.get("candidates") or []
    if not candidates:
        raise GeminiTTSError(
            f"No candidates returned from Vertex native audio. Response: {json.dumps(data)[:2000]}"
        )

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise GeminiTTSError(
            f"No content parts returned from Vertex native audio. Response: {json.dumps(data)[:2000]}"
        )

    audio_b64 = None
    mime_type = "audio/wav"
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data") or {}
        if inline.get("data"):
            audio_b64 = inline.get("data")
            mime_type = inline.get("mimeType", mime_type)
            break
        audio = part.get("audio") or {}
        if audio.get("data"):
            audio_b64 = audio.get("data")
            mime_type = audio.get("mimeType", mime_type)
            break

    if not audio_b64:
        raise GeminiTTSError(
            f"Vertex native audio returned no audio data. Response: {json.dumps(data)[:2000]}"
        )
    return audio_b64, mime_type


def _merge_wav_b64(parts_b64):
    wav_parts = [base64.b64decode(p) for p in parts_b64 if p]
    if not wav_parts:
        raise GeminiTTSError("No audio chunks to merge.")
    if len(wav_parts) == 1:
        return parts_b64[0], "audio/wav"

    with io.BytesIO() as out_buffer:
        with wave.open(out_buffer, "wb") as out_wav:
            params_set = False
            for chunk in wav_parts:
                with io.BytesIO(chunk) as in_buffer:
                    with wave.open(in_buffer, "rb") as in_wav:
                        if not params_set:
                            out_wav.setnchannels(in_wav.getnchannels())
                            out_wav.setsampwidth(in_wav.getsampwidth())
                            out_wav.setframerate(in_wav.getframerate())
                            params_set = True
                        out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))
        merged_bytes = out_buffer.getvalue()
    return base64.b64encode(merged_bytes).decode("ascii"), "audio/wav"


def _is_token_limit_error(message):
    msg = (message or "").lower()
    return "exceeds the maximum number of tokens allowed" in msg or (
        "input token count" in msg and "maximum number of tokens" in msg
    )


def _is_no_audio_error(message):
    msg = (message or "").lower()
    return "returned no audio data" in msg


def _split_chunk_half(chunk):
    words = (chunk or "").split()
    if len(words) < 2:
        return [chunk]
    mid = max(len(words) // 2, 1)
    left = " ".join(words[:mid]).strip()
    right = " ".join(words[mid:]).strip()
    return [part for part in (left, right) if part]


def _build_tts_payload(chunk, tone_style, voice_name, strict_audio_mode=False):
    system_text = (
        "You are an African wise elder storyteller. "
        f"Style: {tone_style} "
        "Speak naturally and clearly. "
        "Read the provided script exactly as written. "
        "Do not summarize, shorten, paraphrase, or add any words."
    )
    if strict_audio_mode:
        system_text += " Output AUDIO only. Do not output text."

    return {
        "systemInstruction": {
            "parts": [{"text": system_text}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": chunk}],
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name,
                    }
                }
            },
        },
    }


def _post_with_retries(req, payload, logger, model_name, chunk_index, chunk_total):
    retries = int(os.environ.get("GEMINI_TTS_RETRIES", "4"))
    base_delay = float(os.environ.get("GEMINI_TTS_RETRY_BASE_DELAY_SEC", "0.8"))
    retry_codes = {408, 409, 425, 429, 500, 502, 503, 504}

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(
                req, data=json.dumps(payload).encode("utf-8"), timeout=60
            ) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            should_retry = exc.code in retry_codes and attempt < retries
            if not should_retry:
                logger.error(
                    "Vertex native audio error %s for model %s chunk %s/%s: %s",
                    exc.code,
                    model_name,
                    chunk_index,
                    chunk_total,
                    body,
                )
                raise GeminiTTSError(f"Vertex native audio error {exc.code}: {body}") from exc

            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning(
                "Transient Vertex error %s on chunk %s/%s (attempt %s/%s). Retrying in %.2fs.",
                exc.code,
                chunk_index,
                chunk_total,
                attempt + 1,
                retries + 1,
                delay,
            )
            time.sleep(delay)
        except urllib.error.URLError as exc:
            if attempt >= retries:
                raise GeminiTTSError(f"Vertex native audio network error: {exc}") from exc
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning(
                "Transient Vertex network error on chunk %s/%s (attempt %s/%s). Retrying in %.2fs. Error: %s",
                chunk_index,
                chunk_total,
                attempt + 1,
                retries + 1,
                delay,
                exc,
            )
            time.sleep(delay)

    raise GeminiTTSError("Vertex native audio request failed after retries.")


def synthesize(text, tone_label):
    logger = logging.getLogger(__name__)
    token = _get_access_token()
    model = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-lite-preview-tts")
    project = "ngd-africa"
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    host = os.environ.get("VERTEX_HOST", f"{location}-aiplatform.googleapis.com")
    method = os.environ.get("GEMINI_TTS_METHOD", "generateContent").lstrip(":")
    voice_name = os.environ.get("GEMINI_TTS_VOICE", "Achernar")
    if not token:
        raise GeminiTTSError(
            "Vertex credentials not available. Set GOOGLE_APPLICATION_CREDENTIALS "
            "to a service account JSON file."
        )
    if not project:
        raise GeminiTTSError("VERTEX_PROJECT is not set.")
    if not model:
        raise GeminiTTSError("GEMINI_TTS_MODEL is not set.")

    model_name = model.replace("models/", "")
    if "/" in model_name:
        model_name = model_name.split("/")[-1]
    base_url = (
        f"https://{host}/v1beta1/projects/"
        f"{project}/locations/{location}/publishers/google/models/"
    )

    url = f"{base_url}{model_name}:{method}"
    tone_style = _resolve_tone_style(tone_label)
    max_words = int(os.environ.get("GEMINI_TTS_MAX_WORDS_PER_CHUNK", "120"))
    story_chunks = _split_story(text, max_words=max_words)
    if not story_chunks:
        raise GeminiTTSError("No text provided for speech synthesis.")

    normalized_wav_parts = []
    min_chunk_words = int(os.environ.get("GEMINI_TTS_MIN_CHUNK_WORDS", "28"))
    no_audio_retries = int(os.environ.get("GEMINI_TTS_NO_AUDIO_RETRIES", "2"))
    index = 0
    while index < len(story_chunks):
        chunk = story_chunks[index]
        audio_b64 = None
        mime_type = None
        chunk_error = None

        for no_audio_attempt in range(no_audio_retries + 1):
            payload = _build_tts_payload(
                chunk=chunk,
                tone_style=tone_style,
                voice_name=voice_name,
                strict_audio_mode=no_audio_attempt > 0,
            )
            req = urllib.request.Request(
                url,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            try:
                data = _post_with_retries(
                    req=req,
                    payload=payload,
                    logger=logger,
                    model_name=model_name,
                    chunk_index=index + 1,
                    chunk_total=len(story_chunks),
                )
                audio_b64, mime_type = _parse_vertex_response(data)
                chunk_error = None
                break
            except GeminiTTSError as exc:
                chunk_error = exc
                if _is_no_audio_error(str(exc)) and no_audio_attempt < no_audio_retries:
                    logger.warning(
                        "No-audio response on chunk %s/%s (attempt %s/%s). Retrying in strict audio mode.",
                        index + 1,
                        len(story_chunks),
                        no_audio_attempt + 1,
                        no_audio_retries + 1,
                    )
                    continue
                break

        if chunk_error:
            chunk_word_count = len(chunk.split())
            if _is_token_limit_error(str(chunk_error)) and chunk_word_count > min_chunk_words:
                split_parts = _split_chunk_half(chunk)
                if len(split_parts) > 1:
                    logger.warning(
                        "Chunk %s/%s exceeded token limit (%s words). Splitting into %s + %s words.",
                        index + 1,
                        len(story_chunks),
                        chunk_word_count,
                        len(split_parts[0].split()),
                        len(split_parts[1].split()),
                    )
                    story_chunks[index:index + 1] = split_parts
                    continue
            raise chunk_error

        try:
            normalized_audio_b64, normalized_mime = _normalize_audio(audio_b64, mime_type)
        except Exception as exc:
            raise GeminiTTSError(f"Failed to normalize Vertex audio output: {exc}") from exc
        if (normalized_mime or "").lower() != "audio/wav":
            raise GeminiTTSError(
                f"Expected WAV output after normalization, got {normalized_mime!r}."
            )
        normalized_wav_parts.append(normalized_audio_b64)
        index += 1

    merged_audio_b64, merged_mime = _merge_wav_b64(normalized_wav_parts)
    return {
        "audio_data": merged_audio_b64,
        "audio_format": merged_mime,
    }
