from rest_framework import serializers
from .models import StudentMark, ReconciliationReport, ReconciliationAnomaly

class StudentMarkSerializer(serializers.ModelSerializer):
    student_registration_number = serializers.CharField(source='student.registration_number', read_only=True)
    student_name = serializers.CharField(source='student.user.get_full_name', read_only=True)
    unit_code = serializers.CharField(source='examination.unit.code', read_only=True)

    class Meta:
        model = StudentMark
        fields = [
            'id', 'student', 'student_registration_number', 'student_name',
            'examination', 'unit_code', 'coursework_mark', 'exam_mark',
            'total_mark', 'grade', 'status', 'submitted_by', 'created_at'
        ]


class ReconciliationAnomalySerializer(serializers.ModelSerializer):
    student_registration_number = serializers.CharField(source='student.registration_number', read_only=True)
    student_name = serializers.CharField(source='student.user.get_full_name', read_only=True)
    anomaly_type_display = serializers.CharField(source='get_anomaly_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)

    class Meta:
        model = ReconciliationAnomaly
        fields = [
            'id', 'student', 'student_registration_number', 'student_name',
            'anomaly_type', 'anomaly_type_display', 'severity', 'severity_display',
            'description', 'evidence_summary', 'is_resolved', 'resolution_notes',
            'resolved_by', 'resolved_at', 'created_at'
        ]


class ReconciliationReportSerializer(serializers.ModelSerializer):
    unit_code = serializers.CharField(source='examination.unit.code', read_only=True)
    unit_name = serializers.CharField(source='examination.unit.name', read_only=True)
    academic_period = serializers.CharField(source='examination.period.name', read_only=True)
    anomalies = ReconciliationAnomalySerializer(many=True, read_only=True)

    class Meta:
        model = ReconciliationReport
        fields = [
            'id', 'examination', 'unit_code', 'unit_name', 'academic_period',
            'total_registered', 'total_attended', 'total_absent',
            'total_booklets_issued', 'total_marks_uploaded', 'total_anomalies',
            'status', 'summary', 'generated_by', 'generated_at', 'anomalies'
        ]
