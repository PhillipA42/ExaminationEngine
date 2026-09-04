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