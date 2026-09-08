from rest_framework import serializers
from academics.serializers import LecturerSerializer, StudentSerializer
from locations.serializers import RoomSerializer
from .models import InvigilatorDuty, ExamAttendance

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

class ExamAttendanceSerializer(serializers.ModelSerializer):
    student_reg_number = serializers.ReadOnlyField(source='student.registration_number')
    student_name = serializers.ReadOnlyField(source='student.user.get_full_name')

    class Meta:
        model = ExamAttendance
        fields = '__all__'