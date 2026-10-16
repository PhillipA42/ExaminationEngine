from django.core.management.base import BaseCommand
from verification.services import VerificationRunner
class Command(BaseCommand):
    help='Run non-destructive system integrity verification.'
    def handle(self,*args,**opts):
        report = VerificationRunner().run()
        self.stdout.write(f'{report.reference}: {report.status}')
        self.stdout.write(f'Total: {report.total_checks} | Passed: {report.passed_checks} | Failed: {report.failed_checks} | Warnings: {report.warnings} | Skipped: {report.skipped_checks}')
        if report.failed_checks:
            self.stdout.write(self.style.ERROR(', '.join(report.summary['failed_codes'])))
