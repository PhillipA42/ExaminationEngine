from rest_framework import serializers
from academics.serializers import UnitSerializer, StudentSerializer
from locations.serializers import RoomSerializer
from .models import ExaminationPeriod, Examination, ExamSchedule, ExamRoomAllocation, StudentExamAllocation

class ExaminationPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExaminationPeriod
        fields = '__all__'

class ExaminationSerializer(serializers.ModelSerializer):
    unit_details = UnitSerializer(source='unit', read_only=True)

    class Meta:
        model = Examination
        fields = '__all__'

class ExamScheduleSerializer(serializers.ModelSerializer):
    unit_code = serializers.ReadOnlyField(source='examination.unit.code')
    unit_name = serializers.ReadOnlyField(source='examination.unit.name')

    class Meta:
        model = ExamSchedule
        fields = '__all__'

class ExamRoomAllocationSerializer(serializers.ModelSerializer):
    room_details = RoomSerializer(source='room', read_only=True)

    class Meta:
        model = ExamRoomAllocation
        fields = '__all__'

class StudentExamAllocationSerializer(serializers.ModelSerializer):
    unit_code = serializers.ReadOnlyField(source='examination.unit.code')
    unit_name = serializers.ReadOnlyField(source='examination.unit.name')
    exam_date = serializers.ReadOnlyField(source='examination.schedule.exam_date')
    start_time = serializers.ReadOnlyField(source='examination.schedule.start_time')
    end_time = serializers.ReadOnlyField(source='examination.schedule.end_time')
    room_name = serializers.ReadOnlyField(source='room.name')
    building_name = serializers.ReadOnlyField(source='room.building.name')

    class Meta:
        model = StudentExamAllocation
        fields = '__all__'