from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager

class CustomUserManager(BaseUserManager):
    def create_user(self, username, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, email, password, **extra_fields)

class User(AbstractUser):
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CustomUserManager()

    def __str__(self):
        return f"{self.username} ({self.email})"

class Role(models.Model):
    ADMIN = 'ADMIN'
    EXAM_OFFICER = 'EXAM_OFFICER'
    ACADEMIC_OFFICER = 'ACADEMIC_OFFICER'
    DEAN = 'DEAN'
    COD = 'COD'
    LECTURER = 'LECTURER'
    STUDENT = 'STUDENT'

    ROLE_CHOICES = [
        (ADMIN, 'System Administrator'),
        (EXAM_OFFICER, 'Examination Officer / Timetabler'),
        (ACADEMIC_OFFICER, 'Academic Officer'),
        (DEAN, 'Dean of School'),
        (COD, 'Chairman of Department'),
        (LECTURER, 'Lecturer / Invigilator'),
        (STUDENT, 'Student'),
    ]

    name = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.get_name_display()

class UserRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user_roles')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='role_users')
    start_date = models.DateField(auto_now_add=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default='ACTIVE')

    class Meta:
        unique_together = ('user', 'role')

    def __str__(self):
        return f"{self.user.username} - {self.role.name}"