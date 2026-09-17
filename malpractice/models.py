from django.db import models
from django.conf import settings
from academics.models import Student, Lecturer
from scheduling.models import Examination
from locations.models import Room

class MalpracticeCase(models.Model):
    SEVERITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ]

    STATUS_CHOICES = [
        ('REPORTED', 'Reported'),
        ('UNDER_INVESTIGATION', 'Under Investigation'),
        ('DISMISS', 'Dismissed'),
        ('SANCTIONED', 'Sanctioned'),
    ]

    case_number = models.CharField(max_length=50, unique=True)
    examination = models.ForeignKey(Examination, on_delete=models.CASCADE, related_name='malpractice_cases')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='malpractice_cases')
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='malpractice_cases')
    reported_by = models.ForeignKey(Lecturer, on_delete=models.SET_NULL, null=True, related_name='reported_malpractices')
    
    incident_type = models.CharField(max_length=100) # e.g., "Unauthorized Material", "Impersonation", "Unauthorized Communication"
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='REPORTED')
    reviewed_by = models.ForeignKey(Lecturer, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_malpractice_cases')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    determination = models.TextField(blank=True)
    
    reported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Case #{self.case_number} - {self.student.registration_number} ({self.incident_type})"

class MalpracticeEvidence(models.Model):
    case = models.ForeignKey(MalpracticeCase, on_delete=models.CASCADE, related_name='evidence_files')
    file = models.FileField(upload_to='malpractice_evidence/%Y/%m/%d/')
    description = models.CharField(max_length=255, blank=True, null=True)
    uploaded_by = models.ForeignKey(Lecturer, null=True, on_delete=models.SET_NULL, related_name='uploaded_malpractice_evidence')
    checksum = models.CharField(max_length=64, editable=False, db_index=True, default='')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Evidence for Case #{self.case.case_number}"


class MalpracticeAudit(models.Model):
    case = models.ForeignKey(MalpracticeCase, on_delete=models.CASCADE, related_name='audit_events')
    actor = models.ForeignKey(Lecturer, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
