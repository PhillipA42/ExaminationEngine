from rest_framework import serializers
from .models import MalpracticeCase, MalpracticeEvidence

class MalpracticeEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MalpracticeEvidence
        fields = '__all__'

class MalpracticeCaseSerializer(serializers.ModelSerializer):
    evidence_files = MalpracticeEvidenceSerializer(many=True, read_only=True)
    student_reg_number = serializers.ReadOnlyField(source='student.registration_number')
    student_name = serializers.ReadOnlyField(source='student.user.get_full_name')
    unit_code = serializers.ReadOnlyField(source='examination.unit.code')
    room_name = serializers.ReadOnlyField(source='room.name')

    class Meta:
        model = MalpracticeCase
        fields = '__all__'