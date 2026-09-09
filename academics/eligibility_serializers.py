"""
Milestone 9 — Eligibility & Notification Serializers
======================================================
DRF serializers for:
  - EligibilityRule CRUD
  - StudentClearance management
  - EligibilityOverride (administrative)
  - EligibilityEvaluation (read-only computed result)
  - Notification (read + mark-read)
  - NotificationDelivery (read-only audit)
"""
from rest_framework import serializers

from academics.models import (
    Student,
    EligibilityRule,
    StudentClearance,
    EligibilityOverride,
    EligibilityEvaluation,
    Notification,
    NotificationDelivery,
)


# ─────────────────────────────────────────────────────────────────────────────
# EligibilityRule
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EligibilityRule
        fields = [
            "id", "name", "code", "is_enabled", "is_mandatory",
            "threshold_value", "description", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ─────────────────────────────────────────────────────────────────────────────
# StudentClearance
# ─────────────────────────────────────────────────────────────────────────────

class StudentClearanceSerializer(serializers.ModelSerializer):
    student_reg = serializers.CharField(source="student.registration_number", read_only=True)
    student_name = serializers.SerializerMethodField()
    clearance_type_display = serializers.CharField(source="get_clearance_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    cleared_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StudentClearance
        fields = [
            "id", "student", "student_reg", "student_name",
            "academic_year", "semester",
            "clearance_type", "clearance_type_display",
            "status", "status_display",
            "source_system", "external_reference", "remarks",
            "cleared_by", "cleared_by_name", "cleared_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "student_reg", "student_name", "clearance_type_display",
                            "status_display", "cleared_by_name", "created_at", "updated_at"]

    def get_student_name(self, obj):
        return obj.student.user.get_full_name() or obj.student.user.username

    def get_cleared_by_name(self, obj):
        if obj.cleared_by:
            return obj.cleared_by.get_full_name() or obj.cleared_by.username
        return None


class BulkClearanceSerializer(serializers.Serializer):
    """Serializer for bulk clearance updates (multiple students, single type/status)."""
    student_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    academic_year = serializers.CharField(max_length=20)
    semester = serializers.IntegerField(min_value=1, max_value=3)
    clearance_type = serializers.ChoiceField(choices=[
        "FINANCIAL", "ADMINISTRATIVE", "DISCIPLINARY", "ATTENDANCE"
    ])
    status = serializers.ChoiceField(choices=["CLEARED", "PENDING", "NOT_CLEARED", "EXEMPTED"])
    remarks = serializers.CharField(required=False, allow_blank=True)


# ─────────────────────────────────────────────────────────────────────────────
# EligibilityOverride
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityOverrideSerializer(serializers.ModelSerializer):
    student_reg = serializers.CharField(source="student.registration_number", read_only=True)
    student_name = serializers.SerializerMethodField()
    period_name = serializers.CharField(source="examination_period.name", read_only=True)
    authorized_by_name = serializers.SerializerMethodField()
    new_status_display = serializers.CharField(source="get_new_status_display", read_only=True)

    class Meta:
        model = EligibilityOverride
        fields = [
            "id", "student", "student_reg", "student_name",
            "examination_period", "period_name",
            "examination",
            "previous_status", "new_status", "new_status_display",
            "reason", "authorized_by", "authorized_by_name",
            "authorized_at", "expires_at", "is_active",
        ]
        read_only_fields = [
            "id", "student_reg", "student_name", "period_name",
            "new_status_display", "authorized_by_name", "authorized_at",
        ]

    def get_student_name(self, obj):
        return obj.student.user.get_full_name() or obj.student.user.username

    def get_authorized_by_name(self, obj):
        if obj.authorized_by:
            return obj.authorized_by.get_full_name() or obj.authorized_by.username
        return None

    def validate(self, data):
        """Ensure the override isn't applied when an active one already exists."""
        student = data.get("student")
        period = data.get("examination_period")
        if student and period and not self.instance:
            from datetime import datetime, timezone
            existing = EligibilityOverride.objects.filter(
                student=student,
                examination_period=period,
                is_active=True,
            ).exists()
            if existing:
                raise serializers.ValidationError(
                    "An active override already exists for this student and period. "
                    "Deactivate the existing override before creating a new one."
                )
        return data


# ─────────────────────────────────────────────────────────────────────────────
# EligibilityEvaluation (read-only)
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityEvaluationSerializer(serializers.ModelSerializer):
    student_reg = serializers.CharField(source="student.registration_number", read_only=True)
    student_name = serializers.SerializerMethodField()
    period_name = serializers.CharField(source="examination_period.name", read_only=True)
    overall_status_display = serializers.CharField(source="get_overall_status_display", read_only=True)

    class Meta:
        model = EligibilityEvaluation
        fields = [
            "id", "student", "student_reg", "student_name",
            "examination_period", "period_name",
            "overall_status", "overall_status_display",
            "is_eligible", "rules_passed", "rules_failed",
            "has_active_override", "evaluation_details", "evaluated_at",
        ]
        read_only_fields = fields

    def get_student_name(self, obj):
        return obj.student.user.get_full_name() or obj.student.user.username


# ─────────────────────────────────────────────────────────────────────────────
# Notification
# ─────────────────────────────────────────────────────────────────────────────

class NotificationDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationDelivery
        fields = ["id", "channel", "status", "sent_at", "error_message"]
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    notification_type_display = serializers.CharField(source="get_notification_type_display", read_only=True)
    deliveries = NotificationDeliverySerializer(many=True, read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id", "title", "message",
            "notification_type", "notification_type_display",
            "related_link", "is_read", "read_at",
            "created_at", "deliveries",
        ]
        read_only_fields = fields
