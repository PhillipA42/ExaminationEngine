from rest_framework import serializers
from academics.models import (
    StudentMark, ResultSubmission, ResultWorkflowAudit, Student, UnitRegistration
)


class StudentMarkDetailSerializer(serializers.ModelSerializer):
    student_registration_number = serializers.CharField(source='student.registration_number', read_only=True)
    student_name = serializers.CharField(source='student.user.get_full_name', read_only=True)

    class Meta:
        model = StudentMark
        fields = [
            'id', 'student', 'student_registration_number', 'student_name',
            'coursework_mark', 'exam_mark', 'total_mark', 'grade',
            'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'total_mark', 'grade', 'status', 'created_at', 'updated_at']


class ResultWorkflowAuditSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = ResultWorkflowAudit
        fields = [
            'id', 'actor', 'actor_name', 'action',
            'previous_status', 'new_status', 'comment', 'timestamp'
        ]

    def get_actor_name(self, obj):
        if obj.actor:
            return obj.actor.get_full_name() or obj.actor.username
        return "System"


class ResultSubmissionListSerializer(serializers.ModelSerializer):
    unit_code = serializers.CharField(source='unit.code', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    lecturer_name = serializers.CharField(source='lecturer.user.get_full_name', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    total_marks_count = serializers.SerializerMethodField()

    class Meta:
        model = ResultSubmission
        fields = [
            'id', 'examination', 'unit_code', 'unit_name',
            'academic_year', 'semester', 'lecturer', 'lecturer_name',
            'department', 'department_name', 'school', 'school_name',
            'status', 'status_display', 'total_marks_count',
            'submitted_at', 'cod_reviewed_at', 'dean_reviewed_at', 'published_at',
            'rejection_reason', 'comments', 'created_at', 'updated_at'
        ]

    def get_total_marks_count(self, obj):
        return obj.student_marks.count()


class ResultSubmissionDetailSerializer(ResultSubmissionListSerializer):
    student_marks = StudentMarkDetailSerializer(many=True, read_only=True)
    audit_trail = ResultWorkflowAuditSerializer(many=True, read_only=True)

    class Meta(ResultSubmissionListSerializer.Meta):
        fields = ResultSubmissionListSerializer.Meta.fields + ['student_marks', 'audit_trail']


class BulkMarkUploadSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)


class WorkflowActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['APPROVE', 'REJECT', 'PUBLISH'])
    comments = serializers.CharField(required=False, allow_blank=True, default='')
    rejection_reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        if data.get('action') == 'REJECT' and not data.get('rejection_reason', '').strip():
            raise serializers.ValidationError({"rejection_reason": "Rejection reason is required when rejecting a submission."})
        return data


class StudentPublishedResultSerializer(serializers.ModelSerializer):
    unit_code = serializers.CharField(source='examination.unit.code', read_only=True)
    unit_name = serializers.CharField(source='examination.unit.name', read_only=True)
    credit_hours = serializers.DecimalField(source='examination.unit.credit_hours', max_digits=4, decimal_places=2, read_only=True)
    academic_year = serializers.CharField(source='examination.period.academic_year', read_only=True)
    semester = serializers.IntegerField(source='examination.period.semester', read_only=True)

    class Meta:
        model = StudentMark
        fields = [
            'id', 'unit_code', 'unit_name', 'credit_hours',
            'academic_year', 'semester',
            'coursework_mark', 'exam_mark', 'total_mark', 'grade',
            'status'
        ]
