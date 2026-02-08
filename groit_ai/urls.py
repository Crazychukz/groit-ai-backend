"""
URL configuration for groit_ai project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from core.api import (
    GeminiModelListView,
    RealtimeTtsView,
    StoryRequestSynthesizeAudioView,
    GeminiTtsTestView,
    StoryRequestCreateView,
    StoryRequestStatusView,
    VertexModelListView,
)
from graphene_django.views import GraphQLView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('graphql/', csrf_exempt(GraphQLView.as_view(graphiql=True))),
    path('api/requests/', StoryRequestCreateView.as_view()),
    path('api/requests/<uuid:request_id>/', StoryRequestStatusView.as_view()),
    path('api/requests/<uuid:request_id>/synthesize-audio/', StoryRequestSynthesizeAudioView.as_view()),
    path('api/models/', GeminiModelListView.as_view()),
    path('api/vertex-models/', VertexModelListView.as_view()),
    path('api/tts-test/', GeminiTtsTestView.as_view()),
    path('api/tts-realtime/', RealtimeTtsView.as_view()),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
