from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ExaminationPeriodViewSet, ExaminationViewSet, ExamScheduleViewSet, StudentTimetableViewSet

router = DefaultRouter()
router.register(r'periods', ExaminationPeriodViewSet)
router.register(r'examinations', ExaminationViewSet)
router.register(r'schedules', ExamScheduleViewSet)
router.register(r'my-timetable', StudentTimetableViewSet, basename='my-timetable')

urlpatterns = [
    path('', include(router.urls)),
]