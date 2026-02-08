import uuid

from django.db import models


class StoryRequest(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    TONE_CHOICES = [
        ('moonlight_elder', 'Moonlight Elder'),
        ('village_fire', 'Village Fire Storyteller'),
        ('wise_judge', 'Wise Judge'),
        ('hopeful_healer', 'Hopeful Healer'),
        ('playful_trickster', 'Playful Trickster'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    input_url = models.URLField(blank=True)
    input_text = models.TextField(blank=True)
    tone = models.CharField(max_length=64, choices=TONE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    source_text = models.TextField(blank=True)
    extracted_data = models.JSONField(blank=True, null=True)
    themes = models.JSONField(blank=True, null=True)
    proverbs = models.JSONField(blank=True, null=True)
    story_text = models.TextField(blank=True)
    moral = models.TextField(blank=True)
    audio_url = models.URLField(blank=True)
    audio_data = models.TextField(blank=True)
    audio_format = models.CharField(max_length=50, blank=True)
    subtitles = models.JSONField(blank=True, null=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"StoryRequest {self.id}"


class Item(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
