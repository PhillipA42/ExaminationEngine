from rest_framework.routers import DefaultRouter
from .views import InvigilatorDutyViewSet, ExamAttendanceViewSet

router = DefaultRouter()
router.register(r'duties', InvigilatorDutyViewSet, basename='invigilator-duties')
router.register(r'attendance', ExamAttendanceViewSet, basename='exam-attendance')

urlpatterns = router.urls
