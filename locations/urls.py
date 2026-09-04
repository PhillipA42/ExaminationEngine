from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CampusViewSet, BuildingViewSet, FloorViewSet, RoomViewSet

router = DefaultRouter()
router.register(r'campuses', CampusViewSet)
router.register(r'buildings', BuildingViewSet)
router.register(r'floors', FloorViewSet)
router.register(r'rooms', RoomViewSet)

urlpatterns = [
    path('', include(router.urls)),
]