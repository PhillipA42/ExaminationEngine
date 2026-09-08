from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from scheduling.models import Examination
from .models import StudentMark, ReconciliationReport, ReconciliationAnomaly
from .reconciliation_serializers import (
    StudentMarkSerializer,
    ReconciliationReportSerializer,
    ReconciliationAnomalySerializer
)
from .reconciliation import ExamReconciliationEngine


class StudentMarkViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Student Marks submitted by lecturers.
    """
    queryset = StudentMark.objects.all().select_related('student__user', 'examination__unit')
    serializer_class = StudentMarkSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        lecturer = getattr(self.request.user, 'lecturer_profile', None)
        serializer.save(submitted_by=lecturer)


class ReconciliationReportViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API for viewing Reconciliation Reports and detected anomalies.
    """
    queryset = ReconciliationReport.objects.all().select_related(
        'examination__unit', 'examination__period'
    ).prefetch_related('anomalies__student__user')
    serializer_class = ReconciliationReportSerializer
    permission_classes = [permissions.IsAuthenticated]


class TriggerReconciliationAPIView(APIView):
    """
    API endpoint to trigger automated 3-way reconciliation for an examination.
    POST /api/academics/reconcile/<examination_id>/
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, examination_id):
        try:
            examination = Examination.objects.get(id=examination_id)
        except Examination.DoesNotExist:
            return Response(
                {"error": f"Examination ID {examination_id} not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        engine = ExamReconciliationEngine(
            examination_id=examination.id,
            generated_by=request.user if request.user.is_authenticated else None
        )
        result = engine.run_reconciliation()

        return Response(result, status=status.HTTP_200_OK)
