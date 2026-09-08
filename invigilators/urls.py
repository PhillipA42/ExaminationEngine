from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InvigilatorDutyViewSet, ExamAttendanceViewSet

router = DefaultRouter()
router.register(r'duties', InvigilatorDutyViewSet, basename='invigilator-duties')
router.register(r'attendance', ExamAttendanceViewSet, basename='exam-attendance')

urlpatterns = [
    path('', include(router.urls)),
]