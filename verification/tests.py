from django.test import TestCase

from verification.models import VerificationCheck
from verification.services import VerificationRunner


class VerificationRunnerTests(TestCase):
    def test_empty_system_is_reported_without_mutating_exam_data(self):
        report = VerificationRunner().run()
        self.assertGreater(report.total_checks, 0)
        self.assertEqual(report.checks.count(), report.total_checks)
        self.assertTrue(VerificationCheck.objects.filter(report=report, category='DATABASE', code='CONNECTIVITY', status='PASS').exists())
        self.assertTrue(VerificationCheck.objects.filter(report=report, status='SKIPPED').exists())
