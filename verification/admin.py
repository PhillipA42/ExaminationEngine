from django.contrib import admin
from .models import SystemVerificationReport, VerificationCheck

class VerificationCheckInline(admin.TabularInline):
    model = VerificationCheck
    extra = 0
    readonly_fields = ('category', 'code', 'name', 'description', 'status', 'severity', 'expected', 'actual', 'details', 'created_at')

@admin.register(SystemVerificationReport)
class SystemVerificationReportAdmin(admin.ModelAdmin):
    list_display = ('reference', 'status', 'total_checks', 'passed_checks', 'failed_checks', 'warnings', 'started_at')
    readonly_fields = ('reference', 'started_at', 'completed_at', 'summary')
    inlines = (VerificationCheckInline,)
