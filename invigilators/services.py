from datetime import datetime
from django.db import transaction
from django.utils import timezone
from academics.models import Lecturer
from scheduling.models import StudentExamAllocation
from .models import InvigilatorDuty, ExamAttendance, ExaminationSession, InvigilationAudit


class InvigilationError(Exception):
    pass


class InvigilationService:
    MAX_INVIGILATORS_PER_ROOM = 4

    @staticmethod
    def _schedule(duty):
        try:
            return duty.examination.schedule
        except duty.examination.__class__.schedule.RelatedObjectDoesNotExist:
            raise InvigilationError('The examination has no finalized schedule.')

    @classmethod
    def validate_duty(cls, examination, lecturer, room, exclude_id=None):
        if lecturer.status != 'ACTIVE': raise InvigilationError('Lecturer is not active.')
        if examination.period.status != 'PUBLISHED': raise InvigilationError('Duties require a published examination period.')
        if not StudentExamAllocation.objects.filter(examination=examination, room=room).exists():
            raise InvigilationError('Room is not an allocated examination session.')
        if InvigilatorDuty.objects.filter(examination=examination, lecturer=lecturer).exclude(pk=exclude_id).exists():
            raise InvigilationError('Lecturer already has a duty for this examination.')
        if InvigilatorDuty.objects.filter(examination=examination, room=room).exclude(pk=exclude_id).count() >= cls.MAX_INVIGILATORS_PER_ROOM:
            raise InvigilationError('This examination room already has the maximum four invigilators.')
        schedule = examination.schedule
        clashes = InvigilatorDuty.objects.filter(lecturer=lecturer, examination__schedule__exam_date=schedule.exam_date,
            examination__schedule__start_time__lt=schedule.end_time, examination__schedule__end_time__gt=schedule.start_time).exclude(pk=exclude_id)
        if clashes.exists(): raise InvigilationError('Lecturer has an overlapping invigilation duty.')

    @classmethod
    def is_active(cls, duty, now=None):
        s = cls._schedule(duty); now = now or timezone.localtime()
        start = timezone.make_aware(datetime.combine(s.exam_date, s.start_time))
        end = timezone.make_aware(datetime.combine(s.exam_date, s.end_time))
        return start <= now <= end

    @classmethod
    def record_attendance(cls, duty, lecturer, student, present, booklet='', remarks=''):
        if duty.lecturer_id != lecturer.id: raise InvigilationError('You are not assigned to this session.')
        if not cls.is_active(duty): raise InvigilationError('Attendance can only be recorded during the examination time window.')
        if not StudentExamAllocation.objects.filter(examination=duty.examination, room=duty.room, student=student).exists():
            raise InvigilationError('Student is not allocated to this examination room.')
        if present and not booklet: raise InvigilationError('A booklet serial is required for a present student.')
        if not present: booklet = booklet or f'ABSENT-EX{duty.examination_id}-ST{student.id}'
        with transaction.atomic():
            other = ExamAttendance.objects.filter(booklet_serial_number=booklet).exclude(examination=duty.examination, student=student)
            if other.exists(): raise InvigilationError('Booklet serial is already assigned.')
            attendance, _ = ExamAttendance.objects.update_or_create(examination=duty.examination, student=student, defaults={'room': duty.room, 'recorded_by': lecturer, 'is_present': present, 'booklet_serial_number': booklet, 'remarks': remarks})
            InvigilationAudit.objects.create(duty=duty, attendance=attendance, actor=lecturer, action='ATTENDANCE_RECORDED', details={'present': present, 'booklet': booklet})
        return attendance
