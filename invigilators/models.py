from django.db import models
from django.conf import settings
from academics.models import Lecturer, Student
from locations.models import Room
from scheduling.models import Examination, ExamSchedule

class InvigilatorDuty(models.Model):
    examination = models.ForeignKey(Examination, on_delete=models.CASCADE, related_name='invigilator_duties')
    lecturer = models.ForeignKey(Lecturer, on_delete=models.CASCADE, related_name='duties')
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='assigned_invigilators')
    role = models.CharField(max_length=50, default='INVIGILATOR') # CHIEF_INVIGILATOR, INVIGILATOR, ASSISTANT
    status = models.CharField(max_length=20, default='ASSIGNED') # ASSIGNED, CONFIRMED, COMPLETED
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('examination', 'lecturer')

    def __str__(self):
        return f"{self.lecturer.user.get_full_name()} -> {self.examination.unit.code} ({self.room.name})"

class ExamAttendance(models.Model):
    examination = models.ForeignKey(Examination, on_delete=models.CASCADE, related_name='attendance_records')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendance_records')
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='attendance_records')
    recorded_by = models.ForeignKey(Lecturer, on_delete=models.SET_NULL, null=True, related_name='recorded_attendances')
    
    # Core Operations: Physical Booklet Serial Number Tracking
    booklet_serial_number = models.CharField(max_length=100, unique=True, help_text="Pre-printed physical booklet serial number")
    is_present = models.BooleanField(default=True)
    check_in_time = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ('examination', 'student')

    def __str__(self):
        return f"{self.student.registration_number} - Booklet #{self.booklet_serial_number}"