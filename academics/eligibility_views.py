"""
Milestone 9 — Eligibility & Notification API Views
=====================================================
DRF ViewSets for:
  - EligibilityRule       (ADMIN / EXAM_OFFICER only)
  - StudentClearance      (ADMIN / EXAM_OFFICER)
  - EligibilityOverride   (ADMIN / EXAM_OFFICER)
  - EligibilityEvaluation (read-only; trigger evaluation via action)
  - Notifications         (authenticated user: list + mark-read)

All admin endpoints re-use the existing RBAC helpers (user_roles).
"""
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from academics.models import (
    Student,
    EligibilityRule,
    StudentClearance,
    EligibilityOverride,
    EligibilityEvaluation,
    Notification,
)
from academics.eligibility_serializers import (
    EligibilityRuleSerializer,
    StudentClearanceSerializer,
    BulkClearanceSerializer,
    EligibilityOverrideSerializer,
    EligibilityEvaluationSerializer,
    NotificationSerializer,
)
from academics.eligibility_services import EligibilityService
from academics.notification_services import NotificationService
from scheduling.models import ExaminationPeriod


# ─────────────────────────────────────────────────────────────────────────────
# Permission helper
# ─────────────────────────────────────────────────────────────────────────────

def _is_admin_or_officer(user) -> bool:
    """Return True if the user has ADMIN or EXAM_OFFICER role, or is a superuser."""
    if user.is_superuser or user.is_staff:
        return True
    roles = set(user.user_roles.values_list("role__name", flat=True))
    return bool(roles & {"ADMIN", "EXAM_OFFICER"})


# ─────────────────────────────────────────────────────────────────────────────
# EligibilityRule ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityRuleViewSet(viewsets.ModelViewSet):
    """CRUD for configurable eligibility policy rules (ADMIN / EXAM_OFFICER only)."""
    queryset = EligibilityRule.objects.all().order_by("id")
    serializer_class = EligibilityRuleSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated()]  # checked inside get_queryset/perform
        return [IsAuthenticated()]

    def list(self, request, *args, **kwargs):
        if not _is_admin_or_officer(request.user):
            return Response({"detail": "Permission denied."}, status=403)
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        if not _is_admin_or_officer(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only administrators or examination officers may manage eligibility rules.")
        serializer.save()

    def perform_update(self, serializer):
        if not _is_admin_or_officer(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only administrators or examination officers may manage eligibility rules.")
        serializer.save()

    def perform_destroy(self, instance):
        if not _is_admin_or_officer(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only administrators or examination officers may delete eligibility rules.")
        instance.delete()


# ─────────────────────────────────────────────────────────────────────────────
# StudentClearance ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class StudentClearanceViewSet(viewsets.ModelViewSet):
    """Manage clearance records for students per semester and type."""
    queryset = StudentClearance.objects.select_related("student__user", "cleared_by").order_by("-created_at")
    serializer_class = StudentClearanceSerializer
    permission_classes = [IsAuthenticated]

    def _check_admin(self):
        if not _is_admin_or_officer(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only ADMIN or EXAM_OFFICER may manage student clearances.")

    def list(self, request, *args, **kwargs):
        if not _is_admin_or_officer(request.user):
            return Response({"detail": "Permission denied."}, status=403)
        # Allow filtering by student, academic_year, semester, clearance_type
        qs = self.queryset
        student_id = request.query_params.get("student")
        if student_id:
            qs = qs.filter(student_id=student_id)
        year = request.query_params.get("academic_year")
        if year:
            qs = qs.filter(academic_year=year)
        sem = request.query_params.get("semester")
        if sem:
            qs = qs.filter(semester=sem)
        ctype = request.query_params.get("clearance_type")
        if ctype:
            qs = qs.filter(clearance_type=ctype)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        self._check_admin()
        serializer.save(
            cleared_by=self.request.user,
            cleared_at=timezone.now(),
        )

    def perform_update(self, serializer):
        self._check_admin()
        serializer.save(
            cleared_by=self.request.user,
            cleared_at=timezone.now(),
        )

    def perform_destroy(self, instance):
        self._check_admin()
        instance.delete()

    @action(detail=False, methods=["post"], url_path="bulk-update")
    def bulk_update(self, request):
        """
        Bulk-update clearance status for multiple students at once.
        POST body: { student_ids: [...], academic_year, semester, clearance_type, status, remarks }
        """
        if not _is_admin_or_officer(request.user):
            return Response({"detail": "Permission denied."}, status=403)

        serializer = BulkClearanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        students = Student.objects.filter(id__in=d["student_ids"])
        updated, created = 0, 0
        now = timezone.now()

        for student in students:
            obj, was_created = StudentClearance.objects.update_or_create(
                student=student,
                academic_year=d["academic_year"],
                semester=d["semester"],
                clearance_type=d["clearance_type"],
                defaults={
                    "status": d["status"],
                    "remarks": d.get("remarks", ""),
                    "cleared_by": request.user,
                    "cleared_at": now,
                    "source_system": "MANUAL",
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

            # Invalidate eligibility cache for this student
            # (force re-evaluation next time eligibility is checked)
            EligibilityService().clear_cache(
                student, type("_Period", (), {"pk": None})()  # placeholder
            )

        return Response({
            "message": f"Bulk clearance applied to {len(students)} students.",
            "created": created,
            "updated": updated,
        })


# ─────────────────────────────────────────────────────────────────────────────
# EligibilityOverride ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityOverrideViewSet(viewsets.ModelViewSet):
    """Administrative override of student eligibility status."""
    queryset = EligibilityOverride.objects.select_related(
        "student__user", "examination_period", "authorized_by"
    ).order_by("-authorized_at")
    serializer_class = EligibilityOverrideSerializer
    permission_classes = [IsAuthenticated]

    def _check_admin(self):
        if not _is_admin_or_officer(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only ADMIN or EXAM_OFFICER may manage eligibility overrides.")

    def list(self, request, *args, **kwargs):
        if not _is_admin_or_officer(request.user):
            return Response({"detail": "Permission denied."}, status=403)
        qs = self.queryset
        student_id = request.query_params.get("student")
        if student_id:
            qs = qs.filter(student_id=student_id)
        period_id = request.query_params.get("examination_period")
        if period_id:
            qs = qs.filter(examination_period_id=period_id)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        self._check_admin()
        student = serializer.validated_data["student"]
        period = serializer.validated_data["examination_period"]
        new_status = serializer.validated_data["new_status"]

        # Capture previous status
        evaluation = EligibilityEvaluation.objects.filter(
            student=student, examination_period=period
        ).first()
        previous_status = evaluation.overall_status if evaluation else "UNKNOWN"

        instance = serializer.save(
            authorized_by=self.request.user,
            previous_status=previous_status,
        )

        # Invalidate cache and re-evaluate
        svc = EligibilityService()
        svc.clear_cache(student, period)
        result = svc.evaluate(student, period, force_refresh=True)

        # Notify the student
        NotificationService().notify_eligibility_update(
            student_user=student.user,
            period_name=period.name,
            new_status=new_status,
        )

    def perform_destroy(self, instance):
        self._check_admin()
        instance.delete()

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """Deactivate an override (soft-delete). Re-evaluates eligibility without the override."""
        self._check_admin()
        override = self.get_object()
        override.is_active = False
        override.save(update_fields=["is_active"])

        # Re-evaluate
        EligibilityService().evaluate(override.student, override.examination_period, force_refresh=True)

        return Response({"message": "Override deactivated. Eligibility re-evaluated."})


# ─────────────────────────────────────────────────────────────────────────────
# EligibilityEvaluation ViewSet (read-only)
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityEvaluationViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view of cached eligibility evaluations. Trigger refresh via /evaluate/ action."""
    queryset = EligibilityEvaluation.objects.select_related(
        "student__user", "examination_period"
    ).order_by("-evaluated_at")
    serializer_class = EligibilityEvaluationSerializer
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        if not _is_admin_or_officer(request.user):
            return Response({"detail": "Permission denied."}, status=403)
        qs = self.queryset
        period_id = request.query_params.get("examination_period")
        if period_id:
            qs = qs.filter(examination_period_id=period_id)
        student_id = request.query_params.get("student")
        if student_id:
            qs = qs.filter(student_id=student_id)
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(overall_status=status_filter)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="evaluate")
    def evaluate(self, request):
        """
        Trigger fresh eligibility evaluation for a student / examination period.
        POST { "student_id": 1, "examination_period_id": 1 }
        """
        if not _is_admin_or_officer(request.user):
            return Response({"detail": "Permission denied."}, status=403)

        student_id = request.data.get("student_id")
        period_id = request.data.get("examination_period_id")
        if not student_id or not period_id:
            return Response({"detail": "student_id and examination_period_id are required."}, status=400)

        try:
            student = Student.objects.get(id=student_id)
            period = ExaminationPeriod.objects.get(id=period_id)
        except (Student.DoesNotExist, ExaminationPeriod.DoesNotExist) as e:
            return Response({"detail": str(e)}, status=404)

        result = EligibilityService().evaluate(student, period, force_refresh=True)
        return Response(result.as_dict())

    @action(detail=False, methods=["post"], url_path="bulk-evaluate")
    def bulk_evaluate(self, request):
        """
        Evaluate all students for a given examination period.
        POST { "examination_period_id": 1 }
        """
        if not _is_admin_or_officer(request.user):
            return Response({"detail": "Permission denied."}, status=403)

        period_id = request.data.get("examination_period_id")
        if not period_id:
            return Response({"detail": "examination_period_id is required."}, status=400)

        try:
            period = ExaminationPeriod.objects.get(id=period_id)
        except ExaminationPeriod.DoesNotExist:
            return Response({"detail": "ExaminationPeriod not found."}, status=404)

        from scheduling.models import StudentExamAllocation
        student_ids = StudentExamAllocation.objects.filter(
            examination__period=period
        ).values_list("student_id", flat=True).distinct()
        students = Student.objects.filter(id__in=student_ids)

        svc = EligibilityService()
        results = svc.bulk_evaluate(students, period, force_refresh=True)

        summary = {
            "ELIGIBLE": 0,
            "CONDITIONALLY_ELIGIBLE": 0,
            "NOT_ELIGIBLE": 0,
            "PENDING_CLEARANCE": 0,
        }
        for r in results.values():
            summary[r.overall_status] = summary.get(r.overall_status, 0) + 1

        return Response({
            "period": period.name,
            "total_evaluated": len(results),
            "summary": summary,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Notification ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Authenticated user's own notifications.
    Supports marking individual or all notifications as read.
    """
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(
            recipient=self.request.user
        ).prefetch_related("deliveries").order_by("-created_at")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        unread_only = request.query_params.get("unread_only")
        if unread_only in ("true", "1", "yes"):
            qs = qs.filter(is_read=False)
        serializer = self.get_serializer(qs[:50], many=True)  # max 50 per page
        return Response({
            "unread_count": NotificationService().get_unread_count(request.user),
            "notifications": serializer.data,
        })

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        """Mark a single notification as read."""
        svc = NotificationService()
        updated = svc.mark_read(pk, request.user)
        if updated:
            return Response({"message": "Notification marked as read."})
        return Response({"message": "Notification already read or not found."}, status=404)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        """Mark all user notifications as read."""
        count = NotificationService().mark_all_read(request.user)
        return Response({"message": f"{count} notification(s) marked as read."})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """Return only the unread count."""
        return Response({"unread_count": NotificationService().get_unread_count(request.user)})
