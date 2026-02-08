from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import StoryRequest
import base64
import os
import uuid
from pathlib import Path

from django.conf import settings
from core.services.gemini_client import list_models
from core.services.vertex_models import VertexModelError, list_models as list_vertex_models
from core.services.gemini_tts import GeminiTTSError, synthesize as gemini_synthesize
from core.services.tts import synthesize as google_synthesize
from core.services.processor import enqueue_request
from core.services.pipeline import TONE_MAP


def _save_audio_to_media(audio_data, audio_format, subdir, filename_base):
    audio_bytes = base64.b64decode(audio_data)
    out_dir = Path(settings.MEDIA_ROOT) / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    mime = (audio_format or '').lower()
    if 'wav' in mime or 'l16' in mime or 'linear16' in mime:
        suffix = 'wav'
    elif 'mpeg' in mime or 'mp3' in mime:
        suffix = 'mp3'
    else:
        suffix = 'bin'
    file_name = f"{filename_base}.{suffix}"
    out_path = out_dir / file_name
    out_path.write_bytes(audio_bytes)
    return file_name, f"/media/{subdir}/{file_name}"


def _synthesize_audio(text, tone):
    provider = os.environ.get('TTS_PROVIDER', 'google').strip().lower()
    errors = []
    allow_gemini_fallback = os.environ.get('ALLOW_GEMINI_TTS_FALLBACK', '').lower() in {'1', 'true', 'yes', 'on'}

    if provider == 'google':
        try:
            result = google_synthesize(text, tone=tone)
            if result:
                return result
        except Exception as exc:
            errors.append(f"Google TTS: {exc}")
        if not allow_gemini_fallback:
            raise GeminiTTSError(" | ".join(errors) or "Google TTS did not return audio.")
        try:
            return gemini_synthesize(text, tone)
        except Exception as exc:
            errors.append(f"Gemini TTS: {exc}")
            raise GeminiTTSError(" | ".join(errors)) from exc

    try:
        return gemini_synthesize(text, tone)
    except Exception as exc:
        errors.append(f"Gemini TTS: {exc}")
    try:
        result = google_synthesize(text, tone=tone)
        if result:
            return result
    except Exception as exc:
        errors.append(f"Google TTS: {exc}")
    raise GeminiTTSError(" | ".join(errors))


def _best_proverb_text(proverbs_value):
    if isinstance(proverbs_value, dict):
        best = proverbs_value.get('best') or {}
        if isinstance(best, dict):
            return (best.get('text') or '').strip()
    return ''


def _theme_list(themes_value):
    if isinstance(themes_value, list):
        return [str(t).strip() for t in themes_value if str(t).strip()]
    return []


def _compose_narration_text(story_text, moral='', themes=None, best_proverb=''):
    parts = []
    base_story = (story_text or '').strip()
    if base_story:
        parts.append(base_story)
    if moral:
        parts.append(f"Moral. {moral.strip()}")
    theme_items = _theme_list(themes or [])
    if theme_items:
        parts.append(f"Themes. {', '.join(theme_items)}.")
    if best_proverb:
        parts.append(f"Best fit proverb. {best_proverb.strip()}")
    return "\n\n".join(parts).strip()


class StoryRequestCreateView(APIView):
    def post(self, request):
        input_url = request.data.get('input_url', '').strip()
        input_text = request.data.get('input_text', '').strip()
        tone = request.data.get('tone', '').strip()

        if not input_url and not input_text:
            return Response(
                {'error': 'Provide input_url or input_text.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if tone not in TONE_MAP:
            return Response(
                {'error': 'Invalid tone.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        story_request = StoryRequest.objects.create(
            input_url=input_url,
            input_text=input_text,
            tone=tone,
            status='queued',
        )
        enqueue_request(story_request.id)

        return Response({'id': str(story_request.id)}, status=status.HTTP_202_ACCEPTED)


class StoryRequestStatusView(APIView):
    def get(self, request, request_id):
        try:
            story_request = StoryRequest.objects.get(id=request_id)
        except StoryRequest.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        audio_url = story_request.audio_url
        if audio_url and audio_url.startswith('/'):
            audio_url = request.build_absolute_uri(audio_url)

        return Response({
            'id': str(story_request.id),
            'status': story_request.status,
            'error': story_request.error_message,
            'story_text': story_request.story_text,
            'moral': story_request.moral,
            'audio_url': audio_url,
            'audio_path': story_request.audio_url,
            'audio_format': story_request.audio_format,
            'has_audio': bool(story_request.audio_data),
        })


class GeminiModelListView(APIView):
    def get(self, request):
        try:
            models = list_models()
        except Exception as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({'models': models})


class VertexModelListView(APIView):
    def get(self, request):
        try:
            models = list_vertex_models()
        except VertexModelError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(models)


class GeminiTtsTestView(APIView):
    def post(self, request):
        sample_text = request.data.get('text') or 'My child, listen well. The drum speaks softly, but its wisdom is deep.'
        try:
            tts_result = _synthesize_audio(sample_text, 'moonlight_elder')
        except GeminiTTSError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not tts_result:
            return Response({'error': 'Gemini native audio not available.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        audio_data = tts_result['audio_data']
        audio_format = tts_result['audio_format']
        audio_url = ''

        try:
            _, audio_url = _save_audio_to_media(
                audio_data=audio_data,
                audio_format=audio_format,
                subdir='tts-tests',
                filename_base=f"test-{uuid.uuid4()}",
            )
        except Exception:
            pass

        return Response({
            'audio_url': audio_url,
            'audio_data': audio_data,
            'audio_format': audio_format,
        })


class StoryRequestSynthesizeAudioView(APIView):
    def post(self, request, request_id):
        try:
            story_request = StoryRequest.objects.get(id=request_id)
        except StoryRequest.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        text = (request.data.get('text') or story_request.story_text or '').strip()
        if not text:
            return Response(
                {'error': 'No story text available for synthesis.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        narration_text = _compose_narration_text(
            story_text=text,
            moral=story_request.moral,
            themes=story_request.themes,
            best_proverb=_best_proverb_text(story_request.proverbs),
        )

        tone = (request.data.get('tone') or story_request.tone or 'moonlight_elder').strip()
        if tone not in TONE_MAP:
            return Response({'error': 'Invalid tone.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            tts_result = _synthesize_audio(narration_text, tone)
        except GeminiTTSError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not tts_result:
            return Response({'error': 'Gemini native audio not available.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        audio_data = tts_result['audio_data']
        audio_format = tts_result['audio_format']
        audio_url = ''
        try:
            _, audio_url = _save_audio_to_media(
                audio_data=audio_data,
                audio_format=audio_format,
                subdir='tts',
                filename_base=str(story_request.id),
            )
            story_request.audio_data = audio_data
            story_request.audio_format = audio_format
            story_request.audio_url = audio_url
            story_request.error_message = ''
            story_request.save(update_fields=['audio_data', 'audio_format', 'audio_url', 'error_message', 'updated_at'])
        except Exception as exc:
            return Response(
                {'error': f'Failed to persist audio: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        absolute_url = audio_url
        if absolute_url.startswith('/'):
            absolute_url = request.build_absolute_uri(absolute_url)

        return Response({
            'id': str(story_request.id),
            'status': story_request.status,
            'tone': tone,
            'audio_url': absolute_url,
            'audio_path': audio_url,
            'audio_format': audio_format,
            'has_audio': bool(audio_data),
        })


class RealtimeTtsView(APIView):
    def post(self, request):
        text = (request.data.get('text') or '').strip()
        if not text:
            return Response({'error': 'text is required.'}, status=status.HTTP_400_BAD_REQUEST)

        tone = (request.data.get('tone') or 'moonlight_elder').strip()
        if tone not in TONE_MAP:
            return Response({'error': 'Invalid tone.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            tts_result = _synthesize_audio(text, tone)
        except GeminiTTSError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not tts_result:
            return Response(
                {'error': 'Gemini native audio not available.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'audio_data': tts_result['audio_data'],
            'audio_format': tts_result['audio_format'],
        })
