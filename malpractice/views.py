from rest_framework import viewsets, permissions, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from .models import MalpracticeCase, MalpracticeEvidence
from .serializers import MalpracticeCaseSerializer, MalpracticeEvidenceSerializer
from .services import MalpracticeService, MalpracticeError
from invigilators.models import InvigilatorDuty
from academics.models import Student

class MalpracticeCaseViewSet(viewsets.ModelViewSet):
    queryset = MalpracticeCase.objects.all()
    serializer_class = MalpracticeCaseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if MalpracticeService._officer(user): return MalpracticeCase.objects.select_related('student__user','examination__unit','room','reported_by__user')
        lecturer = getattr(user, 'lecturer_profile', None)
        return MalpracticeCase.objects.filter(reported_by=lecturer).select_related('student__user','examination__unit','room') if lecturer else MalpracticeCase.objects.none()

    def perform_create(self, serializer):
        raise NotImplementedError('Use the trusted duty report endpoint.')

    @action(detail=False, methods=['post'], url_path='report-from-duty')
    def report_from_duty(self, request):
        lecturer = getattr(request.user, 'lecturer_profile', None)
        duty = InvigilatorDuty.objects.filter(pk=request.data.get('duty_id'), lecturer=lecturer).first()
        student = Student.objects.filter(pk=request.data.get('student_id')).first()
        if not duty or not student: return Response({'detail':'Assigned duty and student are required.'}, status=400)
        try: case = MalpracticeService.report(duty, lecturer, student, request.data.get('incident_type',''), request.data.get('description',''), request.data.get('severity','MEDIUM'))
        except MalpracticeError as exc: return Response({'detail':str(exc)}, status=400)
        return Response(MalpracticeCaseSerializer(case).data, status=201)

    @action(detail=True, methods=['post'])
    def transition(self, request, pk=None):
        try: case = MalpracticeService.transition(self.get_object(), request.user, request.data.get('status'), request.data.get('determination',''))
        except MalpracticeError as exc: return Response({'detail':str(exc)}, status=403)
        return Response(MalpracticeCaseSerializer(case).data)

class MalpracticeEvidenceViewSet(viewsets.ModelViewSet):
    queryset = MalpracticeEvidence.objects.all()
    serializer_class = MalpracticeEvidenceSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self): return MalpracticeEvidence.objects.filter(case__in=MalpracticeCaseViewSet.get_queryset(self)).select_related('case','uploaded_by__user')
    def perform_create(self, serializer):
        case = serializer.validated_data['case']; lecturer = getattr(self.request.user,'lecturer_profile',None)
        if not (MalpracticeService._officer(self.request.user) or case.reported_by_id == getattr(lecturer,'id',None)): raise permissions.PermissionDenied('Not authorized for this case.')
        try: serializer.instance = MalpracticeService.add_evidence(case, lecturer, serializer.validated_data['file'], serializer.validated_data.get('description',''))
        except MalpracticeError as exc: raise serializers.ValidationError(str(exc))
