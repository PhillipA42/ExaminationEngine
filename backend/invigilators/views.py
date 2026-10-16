from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import InvigilatorDuty, ExamAttendance
from .serializers import InvigilatorDutySerializer, ExamAttendanceSerializer
from .services import InvigilationService, InvigilationError
from academics.models import Student

class InvigilatorDutyViewSet(viewsets.ModelViewSet):
    queryset = InvigilatorDuty.objects.all()
    serializer_class = InvigilatorDutySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Allows lecturers to view their assigned duties."""
        user = self.request.user
        if hasattr(user, 'lecturer_profile'):
            return InvigilatorDuty.objects.filter(lecturer=user.lecturer_profile)
        return InvigilatorDuty.objects.none()

    def get_permissions(self):
        if self.action in ('list','retrieve','checkin'): return [permissions.IsAuthenticated()]
        from scheduling.views import IsExamOfficerOrAdmin
        return [IsExamOfficerOrAdmin()]

    @action(detail=True, methods=['post'])
    def checkin(self, request, pk=None):
        duty = self.get_object(); lecturer = getattr(request.user, 'lecturer_profile', None)
        if not lecturer: return Response({'detail':'Lecturer profile required.'}, status=403)
        student = Student.objects.filter(pk=request.data.get('student_id')).first()
        if not student: return Response({'detail':'Student not found.'}, status=404)
        try:
            att = InvigilationService.record_attendance(duty, lecturer, student, request.data.get('is_present', True), request.data.get('booklet_serial_number','').strip(), request.data.get('remarks',''))
        except InvigilationError as exc: return Response({'detail':str(exc)}, status=400)
        return Response(ExamAttendanceSerializer(att).data, status=200)

class ExamAttendanceViewSet(viewsets.ModelViewSet):
    queryset = ExamAttendance.objects.all()
    serializer_class = ExamAttendanceSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_create(self, serializer):
        """Automatically links recording lecturer profile to attendance submission."""
        lecturer = getattr(self.request.user, 'lecturer_profile', None)
        serializer.save(recorded_by=lecturer)
