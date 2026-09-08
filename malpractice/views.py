from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from .models import MalpracticeCase, MalpracticeEvidence
from .serializers import MalpracticeCaseSerializer, MalpracticeEvidenceSerializer

class MalpracticeCaseViewSet(viewsets.ModelViewSet):
    queryset = MalpracticeCase.objects.all()
    serializer_class = MalpracticeCaseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        lecturer = getattr(self.request.user, 'lecturer_profile', None)
        # Generate auto case number if not explicitly set
        count = MalpracticeCase.objects.count() + 1
        case_num = f"MAL-{count:05d}"
        serializer.save(reported_by=lecturer, case_number=case_num)

class MalpracticeEvidenceViewSet(viewsets.ModelViewSet):
    queryset = MalpracticeEvidence.objects.all()
    serializer_class = MalpracticeEvidenceSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]