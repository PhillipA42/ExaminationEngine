from rest_framework import serializers
from academics.serializers import UnitSerializer, StudentSerializer
from locations.serializers import RoomSerializer
from .models import (
    ExaminationPeriod, Examination, ExamSchedule,
    ExamRoomAllocation, StudentExamAllocation, ExamTimeSlot
)
from .services import ExamConstraintChecker, ConflictReport


class ExamTimeSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamTimeSlot
        fields = '__all__'

    def validate(self, attrs):
        start = attrs.get('start_time', getattr(self.instance, 'start_time', None))
        end = attrs.get('end_time', getattr(self.instance, 'end_time', None))
        if start and end and start >= end:
            raise serializers.ValidationError('A time slot must end after it starts.')
        return attrs


class ExaminationPeriodSerializer(serializers.ModelSerializer):
    time_slots = ExamTimeSlotSerializer(many=True, read_only=True)
    total_examinations_count = serializers.ReadOnlyField()
    scheduled_examinations_count = serializers.ReadOnlyField()
    is_published = serializers.ReadOnlyField()

    class Meta:
        model = ExaminationPeriod
        fields = '__all__'


class ExaminationSerializer(serializers.ModelSerializer):
    unit_details = UnitSerializer(source='unit', read_only=True)
    registered_students_count = serializers.ReadOnlyField()
    allocated_students_count = serializers.ReadOnlyField()

    class Meta:
        model = Examination
        fields = '__all__'


class ExamScheduleSerializer(serializers.ModelSerializer):
    unit_code = serializers.ReadOnlyField(source='examination.unit.code')
    unit_name = serializers.ReadOnlyField(source='examination.unit.name')
    period_name = serializers.ReadOnlyField(source='examination.period.name')

    class Meta:
        model = ExamSchedule
        fields = '__all__'

    def validate(self, attrs):
        """
        Hard constraint check before saving manual schedules via API.
        Ensures administrators/API clients cannot bypass timetable rules.
        """
        examination = attrs.get('examination') or getattr(self.instance, 'examination', None)
        exam_date = attrs.get('exam_date') or getattr(self.instance, 'exam_date', None)
        start_time = attrs.get('start_time') or getattr(self.instance, 'start_time', None)
        end_time = attrs.get('end_time') or getattr(self.instance, 'end_time', None)

        if examination and exam_date and start_time and end_time:
            exclude_id = examination.id if self.instance else None
            report = ConflictReport()
            ExamConstraintChecker.check_time_boundaries(examination, exam_date, start_time, end_time, report)
            ExamConstraintChecker.check_student_clashes(examination, exam_date, start_time, end_time, exclude_id, report)
            # Rescheduling also has to protect rooms allocated before the edit.
            for allocation in examination.room_allocations.select_related('room'):
                ExamConstraintChecker.check_room_clashes(
                    allocation.room, exam_date, start_time, end_time, exclude_id, report
                )

            if not report.is_valid:
                errors = [v.message for v in report.hard_violations]
                raise serializers.ValidationError({
                    'conflicts': errors,
                    'conflict_report': report.to_dict()
                })

        return attrs


class ExamRoomAllocationSerializer(serializers.ModelSerializer):
    room_details = RoomSerializer(source='room', read_only=True)
    room_name = serializers.ReadOnlyField(source='room.name')
    room_capacity = serializers.ReadOnlyField(source='room.capacity')

    class Meta:
        model = ExamRoomAllocation
        fields = '__all__'

    def validate(self, attrs):
        examination = attrs.get('examination') or getattr(self.instance, 'examination', None)
        room = attrs.get('room') or getattr(self.instance, 'room', None)
        allocated_capacity = attrs.get('allocated_capacity', getattr(self.instance, 'allocated_capacity', 0))

        if room:
            if room.status != 'ACTIVE':
                raise serializers.ValidationError(f"Room '{room.name}' is inactive and cannot be allocated.")
            if allocated_capacity > room.capacity:
                raise serializers.ValidationError(
                    f"Allocated capacity ({allocated_capacity}) exceeds room capacity ({room.capacity}) for '{room.name}'."
                )
            if allocated_capacity < 0:
                raise serializers.ValidationError("Allocated capacity cannot be negative.")
            if examination and hasattr(examination, 'schedule'):
                schedule = examination.schedule
                report = ConflictReport()
                ExamConstraintChecker.check_room_clashes(
                    room, schedule.exam_date, schedule.start_time, schedule.end_time,
                    examination.id, report
                )
                if not report.is_valid:
                    raise serializers.ValidationError({
                        'conflicts': [v.message for v in report.hard_violations],
                        'conflict_report': report.to_dict(),
                    })
        return attrs


class StudentExamAllocationSerializer(serializers.ModelSerializer):
    unit_code = serializers.ReadOnlyField(source='examination.unit.code')
    unit_name = serializers.ReadOnlyField(source='examination.unit.name')
    exam_date = serializers.ReadOnlyField(source='examination.schedule.exam_date')
    start_time = serializers.ReadOnlyField(source='examination.schedule.start_time')
    end_time = serializers.ReadOnlyField(source='examination.schedule.end_time')
    room_name = serializers.ReadOnlyField(source='room.name')
    building_name = serializers.ReadOnlyField(source='room.building.name')
    student_registration_number = serializers.ReadOnlyField(source='student.registration_number')
    student_name = serializers.ReadOnlyField(source='student.user.get_full_name')

    class Meta:
        model = StudentExamAllocation
        fields = '__all__'
