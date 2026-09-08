from django.db import models
from django.conf import settings

class School(models.Model):
    erp_school_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"

class Department(models.Model):
    erp_department_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='departments')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"

class Course(models.Model):
    erp_course_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='courses')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    level = models.CharField(max_length=50, default='UNDERGRADUATE') # UNDERGRADUATE, POSTGRADUATE, DIPLOMA
    duration = models.IntegerField(default=4) # Duration in years
    status = models.CharField(max_length=20, default='ACTIVE')

    def __str__(self):
        return f"{self.code} - {self.name}"

class Unit(models.Model):
    erp_unit_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='units')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    credit_hours = models.DecimalField(max_digits=4, decimal_places=2, default=3.00)
    year_of_study = models.IntegerField(default=1)
    semester = models.IntegerField(default=1)
    status = models.CharField(max_length=20, default='ACTIVE')

    def __str__(self):
        return f"{self.code} - {self.name}"

class Lecturer(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lecturer_profile')
    erp_staff_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    staff_number = models.CharField(max_length=50, unique=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='lecturers')
    designation = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.staff_number} - {self.user.get_full_name()}"

class Student(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='student_profile')
    erp_student_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    registration_number = models.CharField(max_length=50, unique=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='students')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='students')
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='students')
    year_of_study = models.IntegerField(default=1)
    status = models.CharField(max_length=20, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.registration_number} - {self.user.get_full_name()}"

class UnitRegistration(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='unit_registrations')
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='registered_students')
    academic_year = models.CharField(max_length=20) # e.g., "2026/2027"
    semester = models.IntegerField(default=1)
    registration_status = models.CharField(max_length=20, default='REGISTERED')
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('student', 'unit', 'academic_year', 'semester')

    def __str__(self):
        return f"{self.student.registration_number} registered for {self.unit.code}"


class StudentMark(models.Model):
    """
    Uploaded assessment marks for a student in a specific examination.
    Cross-referenced by the Reconciliation Engine against attendance and registration.
    """
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('SUBMITTED', 'Submitted'),
        ('MODERATED', 'Moderated'),
        ('PUBLISHED', 'Published'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='marks')
    examination = models.ForeignKey('scheduling.Examination', on_delete=models.CASCADE, related_name='student_marks')
    coursework_mark = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Continuous Assessment Mark (out of 30/40)")
    exam_mark = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Final Written Exam Mark (out of 70/60)")
    total_mark = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Combined Total Score (out of 100)")
    grade = models.CharField(max_length=5, blank=True, null=True)
    submitted_by = models.ForeignKey(Lecturer, on_delete=models.SET_NULL, null=True, blank=True, related_name='submitted_marks')
    submission = models.ForeignKey('ResultSubmission', on_delete=models.SET_NULL, null=True, blank=True, related_name='student_marks')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SUBMITTED')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('student', 'examination')

    def save(self, *args, **kwargs):
        self.total_mark = (self.coursework_mark or 0) + (self.exam_mark or 0)
        if not self.grade:
            if self.total_mark >= 70:
                self.grade = 'A'
            elif self.total_mark >= 60:
                self.grade = 'B'
            elif self.total_mark >= 50:
                self.grade = 'C'
            elif self.total_mark >= 40:
                self.grade = 'D'
            else:
                self.grade = 'E'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.registration_number} - {self.examination.unit.code}: {self.total_mark} ({self.grade})"


class ResultSubmission(models.Model):
    """
    Submission and verification lifecycle entity for examination marks per unit/examination context.
    Tracks review stages: DRAFT -> SUBMITTED -> COD_APPROVED -> DEAN_APPROVED -> PUBLISHED (with rejection paths).
    """
    STATUS_CHOICES = [
        ('DRAFT', 'Draft - Mark Entry in Progress'),
        ('SUBMITTED', 'Submitted - Pending COD Review'),
        ('COD_APPROVED', 'COD Approved - Pending Dean Review'),
        ('COD_REJECTED', 'COD Rejected - Returned to Lecturer'),
        ('DEAN_APPROVED', 'Dean Approved - Pending Final Review'),
        ('DEAN_REJECTED', 'Dean Rejected - Returned to Lecturer/COD'),
        ('PUBLISHED', 'Published - Results Released to Students'),
        ('FINAL_REJECTED', 'Exam Authority Rejected - Returned for Correction'),
    ]

    examination = models.ForeignKey('scheduling.Examination', on_delete=models.CASCADE, related_name='result_submissions')
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='result_submissions')
    academic_year = models.CharField(max_length=20)
    semester = models.IntegerField(default=1)
    lecturer = models.ForeignKey(Lecturer, on_delete=models.CASCADE, related_name='result_submissions')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='result_submissions')
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='result_submissions')

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='DRAFT')

    # Workflow timestamps & actors
    submitted_at = models.DateTimeField(null=True, blank=True)
    cod_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cod_reviewed_submissions')
    cod_reviewed_at = models.DateTimeField(null=True, blank=True)
    dean_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='dean_reviewed_submissions')
    dean_reviewed_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='published_submissions')
    published_at = models.DateTimeField(null=True, blank=True)

    rejection_reason = models.TextField(blank=True, null=True)
    comments = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('examination', 'lecturer')
        ordering = ['-updated_at']

    def __str__(self):
        return f"Submission #{self.id} - {self.unit.code} ({self.get_status_display()})"


class ResultWorkflowAudit(models.Model):
    """
    Immutable audit log for result workflow actions, state transitions, and review comments.
    """
    submission = models.ForeignKey(ResultSubmission, on_delete=models.CASCADE, related_name='audit_trail')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='result_audit_actions')
    action = models.CharField(max_length=50)
    previous_status = models.CharField(max_length=30, blank=True, null=True)
    new_status = models.CharField(max_length=30, blank=True, null=True)
    comment = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.submission.unit.code} - {self.action} by {self.actor} at {self.timestamp}"


class ReconciliationReport(models.Model):
    """
    Audit reconciliation report generated for an examination session.
    Summarizes 3-way cross-referencing between UnitRegistration, ExamAttendance, and StudentMarks.
    """
    STATUS_CHOICES = [
        ('CLEAN', 'Clean - 100% Reconciled'),
        ('ANOMALIES_DETECTED', 'Anomalies Detected'),
        ('FLAGGED_CRITICAL', 'Flagged Critical'),
    ]

    examination = models.ForeignKey('scheduling.Examination', on_delete=models.CASCADE, related_name='reconciliation_reports')
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    total_registered = models.IntegerField(default=0)
    total_attended = models.IntegerField(default=0)
    total_absent = models.IntegerField(default=0)
    total_booklets_issued = models.IntegerField(default=0)
    total_marks_uploaded = models.IntegerField(default=0)
    total_anomalies = models.IntegerField(default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='CLEAN')
    summary = models.TextField(blank=True, null=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reconciliation Report #{self.id} - {self.examination.unit.code} ({self.status})"


class ReconciliationAnomaly(models.Model):
    """
    Granular irregularity detected by the reconciliation engine.
    """
    ANOMALY_TYPES = [
        ('MISSING_PHYSICAL_BOOKLET', 'Missing Physical Booklet Serial'),
        ('UNREGISTERED_EXAMINEE', 'Unregistered Examinee Sitting Exam'),
        ('UNSUBMITTED_MARKS', 'Unsubmitted Marks for Attended Candidate'),
        ('GHOST_MARKS', 'Ghost Marks Uploaded for Absent / Non-Attending Student'),
        ('DUPLICATE_BOOKLET_SERIAL', 'Duplicate Physical Booklet Serial Number'),
        ('UNRESOLVED_MALPRACTICE', 'Unresolved Malpractice Case Flagged'),
        ('OTHER_IRREGULARITY', 'Other Examination Discrepancy'),
    ]

    SEVERITY_LEVELS = [
        ('LOW', 'Low - Minor Record Discrepancy'),
        ('MEDIUM', 'Medium - Discrepancy Requiring Investigation'),
        ('HIGH', 'High - Serious Integrity Breach'),
        ('CRITICAL', 'Critical - Unauthorized Examinee / Impersonation / Fraud'),
    ]

    report = models.ForeignKey(ReconciliationReport, on_delete=models.CASCADE, related_name='anomalies')
    student = models.ForeignKey(Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='reconciliation_anomalies')
    anomaly_type = models.CharField(max_length=50, choices=ANOMALY_TYPES)
    severity = models.CharField(max_length=20, choices=SEVERITY_LEVELS, default='MEDIUM')
    description = models.TextField()
    evidence_summary = models.TextField(blank=True, null=True)
    is_resolved = models.BooleanField(default=False)
    resolution_notes = models.TextField(blank=True, null=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_anomalies')
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        student_str = self.student.registration_number if self.student else "General"
        return f"[{self.severity}] {self.get_anomaly_type_display()} - {student_str}"