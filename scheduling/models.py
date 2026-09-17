from django.db import models
from django.conf import settings
from academics.models import Unit, Student
from locations.models import Room

class ExaminationPeriod(models.Model):
    name = models.CharField(max_length=255) # e.g., "2026/2027 Semester 1 Main Exams"
    academic_year = models.CharField(max_length=20) # e.g., "2026/2027"
    semester = models.IntegerField(default=1)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, default='DRAFT') # DRAFT, GENERATING, PUBLISHED, ARCHIVED
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_exam_periods')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_date__lte=models.F('end_date')),
                name='exam_period_dates_ordered',
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"

    @property
    def is_published(self):
        return self.status == 'PUBLISHED'

    @property
    def total_examinations_count(self):
        return self.examinations.count()

    @property
    def scheduled_examinations_count(self):
        return self.examinations.filter(status='SCHEDULED').count()


class ExamTimeSlot(models.Model):
    """
    Configurable daily time slot within an examination period (e.g. Morning, Afternoon).
    Used by the Constraint Timetable Engine and manual scheduling.
    """
    period = models.ForeignKey(ExaminationPeriod, on_delete=models.CASCADE, related_name='time_slots')
    name = models.CharField(max_length=100, default='Morning Session')
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']
        unique_together = ('period', 'start_time', 'end_time')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F('end_time')),
                name='exam_time_slot_times_ordered',
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')})"


class Examination(models.Model):
    period = models.ForeignKey(ExaminationPeriod, on_delete=models.CASCADE, related_name='examinations')
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='examinations')
    examination_type = models.CharField(max_length=50, default='FINAL') # FINAL, CAT, SPECIAL
    duration_minutes = models.IntegerField(default=180) # 3 hours default
    status = models.CharField(max_length=20, default='PENDING') # PENDING, SCHEDULED, COMPLETED
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('period', 'unit', 'examination_type')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name='examination_duration_positive',
            ),
        ]

    def __str__(self):
        return f"{self.unit.code} - {self.examination_type} Exam"

    @property
    def registered_students_count(self):
        from academics.models import UnitRegistration
        return UnitRegistration.objects.filter(
            unit=self.unit,
            academic_year=self.period.academic_year,
            semester=self.period.semester,
            registration_status='REGISTERED'
        ).count()

    @property
    def allocated_students_count(self):
        return self.student_allocations.count()


class ExamSchedule(models.Model):
    examination = models.OneToOneField(Examination, on_delete=models.CASCADE, related_name='schedule')
    exam_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    status = models.CharField(max_length=20, default='SCHEDULED') # SCHEDULED, PUBLISHED, RESCHEDULED
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='published_schedules')

    class Meta:
        indexes = [
            models.Index(fields=['exam_date', 'start_time', 'end_time']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F('end_time')),
                name='exam_schedule_times_ordered',
            ),
        ]

    def __str__(self):
        return f"{self.examination.unit.code} on {self.exam_date} at {self.start_time}"


class ExamRoomAllocation(models.Model):
    examination = models.ForeignKey(Examination, on_delete=models.CASCADE, related_name='room_allocations')
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='allocated_exams')
    allocated_capacity = models.IntegerField()
    status = models.CharField(max_length=20, default='ALLOCATED')

    class Meta:
        unique_together = ('examination', 'room')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(allocated_capacity__gte=0),
                name='exam_room_capacity_nonnegative',
            ),
        ]

    def __str__(self):
        return f"{self.examination.unit.code} -> {self.room.name} (Cap: {self.allocated_capacity})"


class StudentExamAllocation(models.Model):
    examination = models.ForeignKey(Examination, on_delete=models.CASCADE, related_name='student_allocations')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='exam_allocations')
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='student_exam_seats')
    seat_number = models.CharField(max_length=50, null=True, blank=True, default='Pending On-Site') # V1 hides specific seat numbers
    status = models.CharField(max_length=20, default='ALLOCATED')

    class Meta:
        unique_together = ('examination', 'student')

    def __str__(self):
        return f"{self.student.registration_number} -> {self.examination.unit.code} ({self.room.name})"
