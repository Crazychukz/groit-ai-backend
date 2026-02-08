import graphene
from graphene_django import DjangoObjectType

from .models import Item, StoryRequest


class ItemType(DjangoObjectType):
    class Meta:
        model = Item
        fields = ('id', 'name', 'description', 'created_at')


class StoryRequestType(DjangoObjectType):
    class Meta:
        model = StoryRequest
        fields = (
            'id',
            'input_url',
            'input_text',
            'tone',
            'status',
            'source_text',
            'extracted_data',
            'themes',
            'proverbs',
            'story_text',
            'moral',
            'audio_url',
            'audio_data',
            'audio_format',
            'subtitles',
            'error_message',
            'created_at',
            'updated_at',
        )


class Query(graphene.ObjectType):
    items = graphene.List(ItemType)
    story_request = graphene.Field(StoryRequestType, id=graphene.UUID(required=True))
    story_requests = graphene.List(StoryRequestType, limit=graphene.Int())

    def resolve_items(self, info):
        return Item.objects.order_by('-created_at')

    def resolve_story_request(self, info, id):
        try:
            return StoryRequest.objects.get(id=id)
        except StoryRequest.DoesNotExist:
            return None

    def resolve_story_requests(self, info, limit=None):
        qs = StoryRequest.objects.order_by('-created_at')
        if limit:
            qs = qs[:limit]
        return qs
