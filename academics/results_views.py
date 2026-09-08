from rest_framework import viewsets, permissions, status, views
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404

from academics.models import (
    ResultSubmission, StudentMark, Lecturer, Student
)
from academics.results_serializers import (
    ResultSubmissionListSerializer, ResultSubmissionDetailSerializer,
    BulkMarkUploadSerializer, WorkflowActionSerializer,
    StudentPublishedResultSerializer, StudentMarkDetailSerializer
)
from academics.results_services import BulkMarkUploadService, ResultWorkflowService
from authentication.models import Role


class IsLecturerOrOfficer(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        roles = set(request.user.user_roles.values_list('role__name', flat=True))
        return bool(roles & {Role.LECTURER, Role.COD, Role.DEAN, Role.EXAM_OFFICER, Role.ADMIN})


class ResultSubmissionViewSet(viewsets.ModelViewSet):
    """
    REST API ViewSet for Result Submissions with RBAC and scoped querysets:
    - LECTURER: Own assigned submissions
    - COD: Department submissions
    - DEAN: School submissions
    - EXAM_OFFICER / ADMIN: All submissions
    """
    permission_classes = [permissions.IsAuthenticated, IsLecturerOrOfficer]

    def get_serializer_class(self):
        if self.action in ['retrieve', 'update', 'partial_update']:
            return ResultSubmissionDetailSerializer
        return ResultSubmissionListSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return ResultSubmission.objects.all().select_related(
                'examination__unit', 'unit', 'lecturer__user', 'department', 'school'
            )

        roles = set(user.user_roles.values_list('role__name', flat=True))

        if Role.EXAM_OFFICER in roles or Role.ADMIN in roles:
            return ResultSubmission.objects.all().select_related(
                'examination__unit', 'unit', 'lecturer__user', 'department', 'school'
            )

        lecturer = getattr(user, 'lecturer_profile', None)
        if not lecturer:
            return ResultSubmission.objects.none()

        if Role.DEAN in roles:
            return ResultSubmission.objects.filter(
                school=lecturer.department.school
            ).select_related('examination__unit', 'unit', 'lecturer__user', 'department', 'school')

        if Role.COD in roles:
            return ResultSubmission.objects.filter(
                department=lecturer.department
            ).select_related('examination__unit', 'unit', 'lecturer__user', 'department', 'school')

        # Standard Lecturer: own submissions
        return ResultSubmission.objects.filter(
            lecturer=lecturer
        ).select_related('examination__unit', 'unit', 'lecturer__user', 'department', 'school')

    @action(detail=True, methods=['post'], url_path='upload-marks')
    def upload_marks(self, request, pk=None):
        submission = self.get_object()
        # Security: check lecturer ownership or admin
        if submission.status not in ['DRAFT', 'COD_REJECTED', 'DEAN_REJECTED', 'FINAL_REJECTED']:
            return Response(
                {"error": f"Marks cannot be modified while in '{submission.get_status_display()}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not self._can_edit_submission(request.user, submission):
            return Response({"error": "Permission Denied: You cannot upload marks for this submission."}, status=status.HTTP_403_FORBIDDEN)

        serializer = BulkMarkUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        file_obj = serializer.validated_data['file']
        result = BulkMarkUploadService.process_file(file_obj, submission, request.user)

        if not result['success']:
            return Response({
                "status": "error",
                "errors": result['errors'],
                "processed_count": 0
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "status": "success",
            "message": f"Successfully processed and validated {result['processed_count']} marks.",
            "processed_count": result['processed_count'],
            "preview": result['preview']
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='save-marks')
    def save_marks(self, request, pk=None):
        submission = self.get_object()
        if not self._can_edit_submission(request.user, submission):
            return Response({"error": "Permission Denied."}, status=status.HTTP_403_FORBIDDEN)

        marks_data = request.data.get('marks', [])
        if not isinstance(marks_data, list):
            return Response({"error": "Invalid format: 'marks' must be a list of records."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            saved_count = ResultWorkflowService.save_manual_marks(submission, request.user, marks_data)
            return Response({
                "status": "success",
                "message": f"Saved {saved_count} marks successfully.",
                "saved_count": saved_count
            })
        except ValidationError as e:
            return Response({"error": str(e.message if hasattr(e, 'message') else e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        submission = self.get_object()
        if not self._can_edit_submission(request.user, submission):
            return Response({"error": "Permission Denied: Only the assigned lecturer can submit these results."}, status=status.HTTP_403_FORBIDDEN)

        comments = request.data.get('comments', '')
        try:
            updated = ResultWorkflowService.submit_to_cod(submission, request.user, comments=comments)
            return Response({
                "status": "success",
                "message": "Results successfully submitted to Chairman of Department (COD).",
                "submission": ResultSubmissionDetailSerializer(updated).data
            })
        except ValidationError as e:
            return Response({"error": str(e.message if hasattr(e, 'message') else e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='cod-review')
    def cod_review(self, request, pk=None):
        submission = self.get_object()
        serializer = WorkflowActionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        action_type = serializer.validated_data['action']
        comments = serializer.validated_data.get('comments', '')
        rejection_reason = serializer.validated_data.get('rejection_reason', '')

        try:
            updated = ResultWorkflowService.cod_review(
                submission, request.user, action=action_type, comments=comments, rejection_reason=rejection_reason
            )
            return Response({
                "status": "success",
                "message": f"Result submission {updated.get_status_display()}.",
                "submission": ResultSubmissionDetailSerializer(updated).data
            })
        except ValidationError as e:
            return Response({"error": str(e.message if hasattr(e, 'message') else e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='dean-review')
    def dean_review(self, request, pk=None):
        submission = self.get_object()
        serializer = WorkflowActionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        action_type = serializer.validated_data['action']
        comments = serializer.validated_data.get('comments', '')
        rejection_reason = serializer.validated_data.get('rejection_reason', '')

        try:
            updated = ResultWorkflowService.dean_review(
                submission, request.user, action=action_type, comments=comments, rejection_reason=rejection_reason
            )
            return Response({
                "status": "success",
                "message": f"Result submission {updated.get_status_display()}.",
                "submission": ResultSubmissionDetailSerializer(updated).data
            })
        except ValidationError as e:
            return Response({"error": str(e.message if hasattr(e, 'message') else e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        submission = self.get_object()
        serializer = WorkflowActionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        action_type = serializer.validated_data['action']
        comments = serializer.validated_data.get('comments', '')
        rejection_reason = serializer.validated_data.get('rejection_reason', '')

        try:
            updated = ResultWorkflowService.final_review_and_publish(
                submission, request.user, action=action_type, comments=comments, rejection_reason=rejection_reason
            )
            return Response({
                "status": "success",
                "message": f"Result submission {updated.get_status_display()}.",
                "submission": ResultSubmissionDetailSerializer(updated).data
            })
        except ValidationError as e:
            return Response({"error": str(e.message if hasattr(e, 'message') else e)}, status=status.HTTP_400_BAD_REQUEST)

    def _can_edit_submission(self, user, submission):
        if user.is_superuser:
            return True
        lecturer = getattr(user, 'lecturer_profile', None)
        return lecturer is not None and lecturer.id == submission.lecturer_id


class StudentPublishedResultsAPIView(views.APIView):
    """
    API endpoint for students to view their own PUBLISHED results only.
    Draft, rejected, or unapproved marks are strictly excluded.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        student = getattr(request.user, 'student_profile', None)
        if not student and not request.user.is_superuser:
            return Response({"error": "Only registered students can access this endpoint."}, status=status.HTTP_403_FORBIDDEN)

        if student:
            marks = StudentMark.objects.filter(
                student=student,
                status='PUBLISHED'
            ).select_related('examination__unit', 'examination__period').order_by(
                '-examination__period__academic_year', 'examination__period__semester', 'examination__unit__code'
            )
        else:
            # Superuser preview
            marks = StudentMark.objects.filter(status='PUBLISHED').select_related(
                'examination__unit', 'examination__period'
            )

        serializer = StudentPublishedResultSerializer(marks, many=True)
        return Response({
            "student_registration_number": student.registration_number if student else "ALL",
            "published_results": serializer.data
        })
