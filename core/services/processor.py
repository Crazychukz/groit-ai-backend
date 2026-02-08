import base64
import os
import threading
from pathlib import Path

from django.db import close_old_connections
from django.utils import timezone

from core.models import StoryRequest
from .pipeline import run_pipeline
from .gemini_tts import GeminiTTSError, synthesize as gemini_synthesize
from .tts import synthesize as google_synthesize


def _tts_candidates(text):
    text = (text or '').strip()
    if not text:
        return []
    candidates = [text]
    words = text.split()
    # Vertex native TTS currently enforces a 512 token limit.
    # Keep multiple safe fallbacks in descending size.
    for max_words in (320, 260, 220, 180, 140):
        if len(words) > max_words:
            candidates.append(' '.join(words[:max_words]).rstrip())
    # Preserve order but de-duplicate
    return list(dict.fromkeys(candidates))


def _process_request(request_id):
    close_old_connections()
    story_request = StoryRequest.objects.get(id=request_id)
    story_request.status = 'processing'
    story_request.error_message = ''
    story_request.save(update_fields=['status', 'error_message', 'updated_at'])

    try:
        result = run_pipeline(
            story_request.input_url,
            story_request.input_text,
            story_request.tone,
        )
        story_request.source_text = result['source_text']
        story_request.extracted_data = result['facts']
        story_request.themes = result['themes']
        story_request.proverbs = result['proverbs']
        story_request.story_text = result['story_text']
        story_request.moral = result['story_moral'] or result['moral']

        auto_tts_enabled = os.environ.get('AUTO_TTS_AFTER_PIPELINE', '').lower() in {'1', 'true', 'yes', 'on'}
        tts_result = None
        tts_errors = []
        if auto_tts_enabled and story_request.story_text:
            try:
                tts_result = google_synthesize(story_request.story_text, tone=story_request.tone)
            except Exception as exc:
                tts_errors.append(str(exc))
                tts_result = None
            if not tts_result:
                for candidate_text in _tts_candidates(story_request.story_text):
                    try:
                        tts_result = gemini_synthesize(candidate_text, story_request.tone)
                    except GeminiTTSError as exc:
                        tts_errors.append(str(exc))
                        tts_result = None
                    if tts_result:
                        break

        if tts_result:
            story_request.audio_data = tts_result['audio_data']
            story_request.audio_format = tts_result['audio_format']
            story_request.subtitles = tts_result.get('subtitles')

            try:
                audio_bytes = base64.b64decode(story_request.audio_data)
                media_root = Path(os.environ.get('DJANGO_MEDIA_ROOT', ''))
                if not media_root:
                    from django.conf import settings
                    media_root = Path(settings.MEDIA_ROOT)
                out_dir = media_root / 'tts'
                out_dir.mkdir(parents=True, exist_ok=True)
                mime = story_request.audio_format.lower()
                if 'wav' in mime or 'l16' in mime or 'linear16' in mime:
                    suffix = 'wav'
                elif 'mpeg' in mime or 'mp3' in mime:
                    suffix = 'mp3'
                else:
                    suffix = 'bin'
                file_name = f"{story_request.id}.{suffix}"
                out_path = out_dir / file_name
                out_path.write_bytes(audio_bytes)
                story_request.audio_url = f"/media/tts/{file_name}"
            except Exception:
                pass
        elif auto_tts_enabled and story_request.story_text:
            if tts_errors:
                story_request.error_message = (
                    "Audio generation failed. "
                    f"Last error: {tts_errors[-1][:300]}"
                )
            else:
                story_request.error_message = "Audio generation failed."

        story_request.status = 'completed'
        story_request.updated_at = timezone.now()
        story_request.save()
    except Exception as exc:
        story_request.status = 'failed'
        story_request.error_message = str(exc)
        story_request.updated_at = timezone.now()
        story_request.save()
    finally:
        close_old_connections()


def enqueue_request(request_id):
    thread = threading.Thread(target=_process_request, args=(request_id,), daemon=True)
    thread.start()
