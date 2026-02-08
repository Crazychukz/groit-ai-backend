from django.contrib import admin

from .models import Item, StoryRequest


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)


@admin.register(StoryRequest)
class StoryRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'tone', 'status', 'created_at')
    list_filter = ('status', 'tone')
    search_fields = ('id', 'input_url')
