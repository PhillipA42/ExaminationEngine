from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from .models import InvigilatorDuty, ExamAttendance
from .serializers import InvigilatorDutySerializer, ExamAttendanceSerializer

class InvigilatorDutyViewSet(viewsets.ModelViewSet):
    queryset = InvigilatorDuty.objects.all()
    serializer_class = InvigilatorDutySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Allows lecturers to view their assigned duties."""
        user = self.request.user
        if hasattr(user, 'lecturer_profile'):
            return InvigilatorDuty.objects.filter(lecturer=user.lecturer_profile)
        return InvigilatorDuty.objects.all()

class ExamAttendanceViewSet(viewsets.ModelViewSet):
    queryset = ExamAttendance.objects.all()
    serializer_class = ExamAttendanceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        """Automatically links recording lecturer profile to attendance submission."""
        lecturer = getattr(self.request.user, 'lecturer_profile', None)
        serializer.save(recorded_by=lecturer)