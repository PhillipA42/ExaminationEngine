from django.core.management.base import BaseCommand
from scheduling.models import Examination, ExaminationPeriod
from academics.reconciliation import ExamReconciliationEngine


class Command(BaseCommand):
    help = 'Executes 3-way reconciliation across UnitRegistration, ExamAttendance, and StudentMarks to detect anomalies.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--exam_id',
            type=int,
            help='Specific Examination ID to reconcile.',
        )
        parser.add_argument(
            '--period_id',
            type=int,
            help='Reconcile all examinations in a specific ExaminationPeriod ID.',
        )

    def handle(self, *args, **options):
        exam_id = options.get('exam_id')
        period_id = options.get('period_id')

        if exam_id:
            examinations = Examination.objects.filter(id=exam_id)
            if not examinations.exists():
                self.stdout.write(self.style.ERROR(f"Examination with ID {exam_id} does not exist."))
                return
        elif period_id:
            examinations = Examination.objects.filter(period_id=period_id)
            if not examinations.exists():
                self.stdout.write(self.style.ERROR(f"No examinations found in Examination Period ID {period_id}."))
                return
        else:
            # Reconcile all examinations
            examinations = Examination.objects.all()

        self.stdout.write(self.style.WARNING(f"Starting reconciliation for {examinations.count()} examination(s)..."))

        for exam in examinations:
            engine = ExamReconciliationEngine(examination_id=exam.id)
            result = engine.run_reconciliation()

            status_style = self.style.SUCCESS if result['status'] == 'CLEAN' else self.style.ERROR
            self.stdout.write(f"\n=======================================================")
            self.stdout.write(f"Exam: {exam.unit.code} - {exam.unit.name} (Period: {exam.period.name})")
            self.stdout.write(status_style(f"Status: {result['status']} | Report ID: #{result['report_id']}"))
            self.stdout.write(
                f"Metrics: Registered: {result['total_registered']} | "
                f"Attended: {result['total_attended']} | "
                f"Absent: {result['total_absent']} | "
                f"Booklets: {result['total_booklets_issued']} | "
                f"Marks: {result['total_marks_uploaded']}"
            )
            self.stdout.write(f"Total Anomalies Detected: {result['total_anomalies']}")

            if result['anomalies']:
                self.stdout.write(self.style.WARNING("\nDetected Anomalies:"))
                for a in result['anomalies']:
                    self.stdout.write(
                        f"  - [{a['severity']}] {a['type_display']} "
                        f"(Student: {a['student_reg'] or 'General'}): {a['description']}"
                    )

        self.stdout.write(self.style.SUCCESS("\nReconciliation batch completed!"))
