from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MalpracticeCaseViewSet, MalpracticeEvidenceViewSet

router = DefaultRouter()
router.register(r'cases', MalpracticeCaseViewSet, basename='malpractice-cases')
router.register(r'evidence', MalpracticeEvidenceViewSet, basename='malpractice-evidence')

urlpatterns = [
    path('', include(router.urls)),
]