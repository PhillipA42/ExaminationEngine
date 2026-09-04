from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import ExaminationPeriod, Examination, ExamSchedule, ExamRoomAllocation, StudentExamAllocation
from .serializers import (
    ExaminationPeriodSerializer, ExaminationSerializer, ExamScheduleSerializer,
    ExamRoomAllocationSerializer, StudentExamAllocationSerializer
)
from .engine import TimetableSchedulerEngine

class ExaminationPeriodViewSet(viewsets.ModelViewSet):
    queryset = ExaminationPeriod.objects.all()
    serializer_class = ExaminationPeriodSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='generate-timetable')
    def generate_timetable(self, request, pk=None):
        """API Endpoint to trigger constraint optimization engine."""
        period = self.get_object()
        scheduler = TimetableSchedulerEngine(period_id=period.id)
        success = scheduler.generate_timetable()
        
        if success:
            return Response({"message": "Timetable generated successfully without clashes."}, status=status.HTTP_200_OK)
        return Response({"error": "Failed to generate timetable."}, status=status.HTTP_400_BAD_REQUEST)

class ExaminationViewSet(viewsets.ModelViewSet):
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer
    permission_classes = [permissions.IsAuthenticated]

class ExamScheduleViewSet(viewsets.ModelViewSet):
    queryset = ExamSchedule.objects.all()
    serializer_class = ExamScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

class StudentTimetableViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Personalized Student Timetable Endpoint:
    Filters schedule by logged-in student's registered units.
    """
    serializer_class = StudentExamAllocationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'student_profile'):
            return StudentExamAllocation.objects.filter(student=user.student_profile)
        return StudentExamAllocation.objects.none()