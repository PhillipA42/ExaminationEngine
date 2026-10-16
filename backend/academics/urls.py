from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SchoolViewSet, DepartmentViewSet, CourseViewSet,
    UnitViewSet, LecturerViewSet, StudentViewSet, UnitRegistrationViewSet
)
from .reconciliation_views import (
    StudentMarkViewSet,
    ReconciliationReportViewSet,
    TriggerReconciliationAPIView
)
from .results_views import (
    ResultSubmissionViewSet,
    StudentPublishedResultsAPIView
)
from .eligibility_views import (
    EligibilityRuleViewSet,
    StudentClearanceViewSet,
    EligibilityOverrideViewSet,
    EligibilityEvaluationViewSet,
    NotificationViewSet,
)

router = DefaultRouter()
router.register(r'schools', SchoolViewSet)
router.register(r'departments', DepartmentViewSet)
router.register(r'courses', CourseViewSet)
router.register(r'units', UnitViewSet)
router.register(r'lecturers', LecturerViewSet)
router.register(r'students', StudentViewSet)
router.register(r'unit-registrations', UnitRegistrationViewSet)
router.register(r'marks', StudentMarkViewSet, basename='student-marks')
router.register(r'submissions', ResultSubmissionViewSet, basename='result-submissions')
router.register(r'reconciliation-reports', ReconciliationReportViewSet, basename='reconciliation-reports')

# Milestone 9 — Eligibility & Notification endpoints
router.register(r'eligibility-rules', EligibilityRuleViewSet, basename='eligibility-rules')
router.register(r'clearances', StudentClearanceViewSet, basename='student-clearances')
router.register(r'eligibility-overrides', EligibilityOverrideViewSet, basename='eligibility-overrides')
router.register(r'eligibility-evaluations', EligibilityEvaluationViewSet, basename='eligibility-evaluations')
router.register(r'notifications', NotificationViewSet, basename='notifications')

urlpatterns = [
    path('reconcile/<int:examination_id>/', TriggerReconciliationAPIView.as_view(), name='trigger-reconciliation'),
    path('student-published-results/', StudentPublishedResultsAPIView.as_view(), name='student-published-results-api'),
    path('', include(router.urls)),
]