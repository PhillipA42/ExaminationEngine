from django.db import models
from django.conf import settings

class SystemVerificationReport(models.Model):
    STATUS_CHOICES = [('RUNNING', 'Running'), ('PASSED', 'Passed'), ('PASSED_WITH_WARNINGS', 'Passed with warnings'), ('FAILED', 'Failed')]
    reference = models.CharField(max_length=40, unique=True)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='RUNNING')
    total_checks = models.PositiveIntegerField(default=0); passed_checks = models.PositiveIntegerField(default=0)
    failed_checks = models.PositiveIntegerField(default=0); warnings = models.PositiveIntegerField(default=0)
    skipped_checks = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict)

class VerificationCheck(models.Model):
    CHECK_STATUSES = [('PASS', 'Pass'), ('FAIL', 'Fail'), ('WARNING', 'Warning'), ('SKIPPED', 'Skipped')]
    report = models.ForeignKey(SystemVerificationReport, on_delete=models.CASCADE, related_name='checks')
    category = models.CharField(max_length=50); code = models.CharField(max_length=80); name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=CHECK_STATUSES); severity = models.CharField(max_length=12, default='INFO')
    expected = models.TextField(blank=True); actual = models.TextField(blank=True); details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: indexes = [models.Index(fields=['report','status'])]
