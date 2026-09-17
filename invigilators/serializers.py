from rest_framework import serializers
from academics.serializers import LecturerSerializer, StudentSerializer
from locations.serializers import RoomSerializer
from .models import InvigilatorDuty, ExamAttendance
from .services import InvigilationService, InvigilationError

class InvigilatorDutySerializer(serializers.ModelSerializer):
    lecturer_details = LecturerSerializer(source='lecturer', read_only=True)
    room_details = RoomSerializer(source='room', read_only=True)
    unit_code = serializers.ReadOnlyField(source='examination.unit.code')
    unit_name = serializers.ReadOnlyField(source='examination.unit.name')
    exam_date = serializers.ReadOnlyField(source='examination.schedule.exam_date')
    start_time = serializers.ReadOnlyField(source='examination.schedule.start_time')

    class Meta:
        model = InvigilatorDuty
        fields = '__all__'

    def validate(self, attrs):
        exam = attrs.get('examination') or getattr(self.instance, 'examination', None)
        lecturer = attrs.get('lecturer') or getattr(self.instance, 'lecturer', None)
        room = attrs.get('room') or getattr(self.instance, 'room', None)
        if exam and lecturer and room:
            try: InvigilationService.validate_duty(exam, lecturer, room, getattr(self.instance, 'id', None))
            except InvigilationError as exc: raise serializers.ValidationError({'detail': str(exc)})
        return attrs

class ExamAttendanceSerializer(serializers.ModelSerializer):
    student_reg_number = serializers.ReadOnlyField(source='student.registration_number')
    student_name = serializers.ReadOnlyField(source='student.user.get_full_name')

    class Meta:
        model = ExamAttendance
        fields = '__all__'

    def validate(self, attrs):
        # Attendance writes are deliberately limited to the session service/API.
        if self.context.get('request') and not self.context['request'].user.is_superuser:
            raise serializers.ValidationError('Use the assigned-duty check-in endpoint.')
        return attrs
