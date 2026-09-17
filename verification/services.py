"""Non-destructive, system-wide verification which reuses M2's constraint engine."""
import io
import uuid

from django.core.management import call_command
from django.db import connection, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from academics.models import Course, Department, ReconciliationReport, School, Student, Unit, UnitRegistration
from authentication.models import User
from invigilators.models import ExamAttendance, InvigilatorDuty
from locations.models import Room
from malpractice.models import MalpracticeCase, MalpracticeEvidence
from scheduling.models import ExamRoomAllocation, ExamSchedule, Examination, ExaminationPeriod, StudentExamAllocation
from scheduling.services import ExamConstraintChecker
from .models import SystemVerificationReport, VerificationCheck


class VerificationRunner:
    """Reports problems only. It does not repair or mutate source examination records."""
    def __init__(self, user=None):
        self.user, self.results = user, []

    def record(self, category, code, name, status, severity='HIGH', expected='', actual='', description='', **details):
        self.results.append({'category': category, 'code': code, 'name': name, 'status': status,
            'severity': severity, 'expected': str(expected), 'actual': str(actual),
            'description': description, 'details': details})

    def check(self, category, code, name, condition, **kwargs):
        self.record(category, code, name, 'PASS' if condition else 'FAIL', **kwargs)

    def warning(self, category, code, name, **kwargs):
        self.record(category, code, name, 'WARNING', severity='MEDIUM', **kwargs)

    def _database(self):
        try:
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                connected = cursor.fetchone()[0] == 1
            self.check('DATABASE', 'CONNECTIVITY', 'Database connectivity', connected, expected='Database accepts a query')
            with transaction.atomic():
                sid = transaction.savepoint(); transaction.savepoint_commit(sid)
            self.check('DATABASE', 'TRANSACTIONS', 'Transactional savepoints', True, expected='Savepoints are supported')
        except Exception as exc:
            self.check('DATABASE', 'CONNECTIVITY', 'Database connectivity', False, severity='CRITICAL', actual=exc)
        try:
            call_command('check', stdout=io.StringIO(), stderr=io.StringIO())
            self.check('DATABASE', 'DJANGO_CHECKS', 'Django system checks', True)
        except Exception as exc:
            self.check('DATABASE', 'DJANGO_CHECKS', 'Django system checks', False, severity='CRITICAL', actual=exc)

    def _structure(self):
        missing = sum(m.objects.filter(Q(code__isnull=True) | Q(code='')).count() for m in (School, Department, Course, Unit))
        self.check('ACADEMICS', 'REQUIRED_CODES', 'Academic records have required identifiers', missing == 0, expected=0, actual=missing)
        duplicates = Unit.objects.values('course_id', 'code').annotate(n=Count('id')).filter(n__gt=1).count()
        self.check('ACADEMICS', 'DUPLICATE_UNIT_CODES', 'Unit codes are unique within a course', duplicates == 0, expected=0, actual=duplicates)
        mismatched = Student.objects.exclude(course__department=F('department')).count() + Student.objects.exclude(department__school=F('school')).count()
        self.check('ACADEMICS', 'STUDENT_HIERARCHY', 'Student course, department and school agree', mismatched == 0, expected=0, actual=mismatched)
        floor_mismatches = Room.objects.exclude(floor__building=F('building')).count()
        self.check('LOCATIONS', 'ROOM_HIERARCHY', 'Room building agrees with its floor', floor_mismatches == 0, expected=0, actual=floor_mismatches)
        bad_capacity = Room.objects.filter(capacity__lte=0).count()
        self.check('LOCATIONS', 'ROOM_CAPACITY', 'Rooms have a positive capacity', bad_capacity == 0, expected=0, actual=bad_capacity)
        duplicate_rooms = Room.objects.values('building_id', 'room_number').annotate(n=Count('id')).filter(n__gt=1).count()
        self.check('LOCATIONS', 'DUPLICATE_ROOM_IDENTIFIERS', 'Room numbers are unique within a building', duplicate_rooms == 0, expected=0, actual=duplicate_rooms)

    def _identity_and_allocation(self):
        profiles = User.objects.filter(Q(lecturer_profile__isnull=True) & Q(student_profile__isnull=True), is_superuser=False).count()
        if profiles:
            self.warning('AUTHENTICATION', 'PROFILELESS_USERS', 'Users without student or lecturer profile', actual=profiles, description='Administrative-only accounts may be intentional.')
        inactive = InvigilatorDuty.objects.filter(Q(lecturer__status__iexact='INACTIVE') | Q(lecturer__user__is_active=False)).count()
        self.check('INVIGILATION', 'ACTIVE_DUTY_HOLDERS', 'Duties have active lecturers', inactive == 0, expected=0, actual=inactive)
        invalid = StudentExamAllocation.objects.exclude(student__unit_registrations__unit=F('examination__unit'), student__unit_registrations__academic_year=F('examination__period__academic_year'), student__unit_registrations__semester=F('examination__period__semester'), student__unit_registrations__registration_status='REGISTERED').count()
        self.check('STUDENT_ALLOCATION', 'REGISTRATION_CHAIN', 'Allocated candidates have unit registrations', invalid == 0, expected=0, actual=invalid)
        room_links = StudentExamAllocation.objects.exclude(room__allocated_exams__examination=F('examination')).count()
        self.check('ROOM_ALLOCATION', 'ALLOCATION_ROOM_LINK', 'Student allocation room belongs to its examination', room_links == 0, expected=0, actual=room_links)
        capacity_errors = 0
        for allocation in ExamRoomAllocation.objects.select_related('room'):
            assigned = StudentExamAllocation.objects.filter(examination=allocation.examination, room=allocation.room).count()
            if allocation.allocated_capacity > allocation.room.capacity or assigned > allocation.allocated_capacity:
                capacity_errors += 1
        self.check('ROOM_ALLOCATION', 'CAPACITY', 'Room allocations do not exceed capacity', capacity_errors == 0, expected=0, actual=capacity_errors)

    def _operations(self):
        invalid_attendance = ExamAttendance.objects.exclude(student__exam_allocations__examination=F('examination'), student__exam_allocations__room=F('room')).count()
        self.check('ATTENDANCE', 'ALLOCATION_CHAIN', 'Attendance matches student examination allocation', invalid_attendance == 0, expected=0, actual=invalid_attendance)
        unauthorized = ExamAttendance.objects.filter(recorded_by__isnull=False).exclude(recorded_by__duties__examination=F('examination'), recorded_by__duties__room=F('room')).count()
        self.check('ATTENDANCE', 'AUTHORIZED_RECORDER', 'Attendance recorder has the room duty', unauthorized == 0, expected=0, actual=unauthorized)
        serials = ExamAttendance.objects.values('booklet_serial_number').annotate(n=Count('id')).filter(n__gt=1).count()
        self.check('BOOKLET', 'SERIAL_UNIQUENESS', 'Booklet serial numbers are unique', serials == 0, expected=0, actual=serials)
        bad_cases = MalpracticeCase.objects.exclude(student__exam_allocations__examination=F('examination'), student__exam_allocations__room=F('room')).count()
        self.check('MALPRACTICE', 'CASE_RELATIONSHIPS', 'Malpractice case relationships match allocation', bad_cases == 0, expected=0, actual=bad_cases, description='Neutral relationship verification; it makes no finding about misconduct.')
        empty_evidence = MalpracticeEvidence.objects.filter(file='').count()
        self.check('MALPRACTICE', 'EVIDENCE_FILES', 'Evidence entries reference files', empty_evidence == 0, expected=0, actual=empty_evidence)

    def _scheduling(self):
        missing = Examination.objects.filter(status='SCHEDULED', schedule__isnull=True).count()
        self.check('SCHEDULING', 'SCHEDULED_EXAMS_HAVE_SCHEDULE', 'Scheduled exams have schedules', missing == 0, expected=0, actual=missing)
        outside = ExamSchedule.objects.filter(Q(exam_date__lt=F('examination__period__start_date')) | Q(exam_date__gt=F('examination__period__end_date'))).count()
        self.check('SCHEDULING', 'PERIOD_BOUNDS', 'Schedules are within the examination period', outside == 0, expected=0, actual=outside)
        periods = ExaminationPeriod.objects.all()
        if not periods.exists():
            self.record('SCHEDULING', 'NO_PERIODS', 'Timetable verification', 'SKIPPED', severity='INFO', description='No examination periods exist.')
        for period in periods:
            audit = ExamConstraintChecker.validate_period_entire_timetable(period)
            self.check('SCHEDULING', f'PERIOD_{period.pk}_HARD_CONSTRAINTS', f'{period.name}: hard timetable constraints', audit.is_valid, expected=0, actual=len(audit.hard_violations), period_id=period.pk)
            if audit.soft_warnings:
                self.warning('SCHEDULING', f'PERIOD_{period.pk}_SOFT_CONSTRAINTS', f'{period.name}: soft timetable constraints', actual=len(audit.soft_warnings), period_id=period.pk)

    def _reconciliation(self):
        """Verify M6 persisted output without re-running it or creating duplicate reports."""
        inconsistent = sum(report.total_anomalies != report.anomalies.count() for report in ReconciliationReport.objects.prefetch_related('anomalies'))
        self.check('RECONCILIATION', 'REPORT_TOTALS', 'M6 report totals match persisted anomalies', inconsistent == 0, expected=0, actual=inconsistent)

    def run(self):
        report = SystemVerificationReport.objects.create(reference=f'VER-{uuid.uuid4().hex[:16].upper()}', initiated_by=self.user)
        self._database(); self._structure(); self._identity_and_allocation(); self._operations(); self._scheduling(); self._reconciliation()
        VerificationCheck.objects.bulk_create([VerificationCheck(report=report, **row) for row in self.results])
        counts = {s: sum(row['status'] == s for row in self.results) for s in ('PASS', 'FAIL', 'WARNING', 'SKIPPED')}
        report.total_checks = len(self.results); report.passed_checks = counts['PASS']; report.failed_checks = counts['FAIL']; report.warnings = counts['WARNING']; report.skipped_checks = counts['SKIPPED']
        report.status = 'FAILED' if counts['FAIL'] else ('PASSED_WITH_WARNINGS' if counts['WARNING'] else 'PASSED')
        report.completed_at = timezone.now(); report.summary = {'counts': counts, 'failed_codes': [r['code'] for r in self.results if r['status'] == 'FAIL']}; report.save()
        return report
