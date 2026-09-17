import json
from datetime import date, time, timedelta
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from authentication.models import Role, UserRole
from academics.models import School, Department, Course, Unit, Student, UnitRegistration
from locations.models import Campus, Building, Floor, Room
from scheduling.models import (
    ExaminationPeriod, Examination, ExamSchedule,
    ExamRoomAllocation, StudentExamAllocation, ExamTimeSlot
)
from scheduling.engine import TimetableSchedulerEngine
from scheduling.services import ExamConstraintChecker, ConflictReport

User = get_user_model()


class SchedulingMilestone2BaseTestCase(TestCase):
    """Base setup for Milestone 2 scheduling and constraint testing."""

    def setUp(self):
        self.client = Client()

        # 1. Roles
        self.role_officer, _ = Role.objects.get_or_create(name=Role.EXAM_OFFICER)
        self.role_student, _ = Role.objects.get_or_create(name=Role.STUDENT)

        # 2. Location Tree
        self.campus = Campus.objects.create(code='MAIN', name='Main Campus')
        self.building = Building.objects.create(campus=self.campus, code='SC', name='Science Complex')
        self.floor = Floor.objects.create(building=self.building, floor_number=1, name='1st Floor')

        self.room_50 = Room.objects.create(building=self.building, floor=self.floor, name='Hall A', room_number='HA-1', capacity=50, status='ACTIVE')
        self.room_40 = Room.objects.create(building=self.building, floor=self.floor, name='Hall B', room_number='HB-1', capacity=40, status='ACTIVE')
        self.room_30 = Room.objects.create(building=self.building, floor=self.floor, name='Lab 1', room_number='L1-1', capacity=30, status='ACTIVE')

        # 3. Academic Hierarchy
        self.school = School.objects.create(code='SIST', name='School of Information Sciences')
        self.dept = Department.objects.create(school=self.school, code='CS', name='Computer Science')
        self.course = Course.objects.create(department=self.dept, code='BSCCS', name='BSc Computer Science')

        self.unit_a = Unit.objects.create(course=self.course, code='CSC401', name='Software Architecture', credit_hours=3.0)
        self.unit_b = Unit.objects.create(course=self.course, code='CSC402', name='Database Administration', credit_hours=3.0)
        self.unit_c = Unit.objects.create(course=self.course, code='CSC403', name='Network Security', credit_hours=3.0)

        # 4. Examination Period
        self.period = ExaminationPeriod.objects.create(
            name='2026/2027 Main Exams',
            academic_year='2026/2027',
            semester=1,
            start_date=date(2026, 10, 5),  # Monday
            end_date=date(2026, 10, 16),   # Friday
            status='DRAFT'
        )

        # 5. Officer User
        self.officer_user = User.objects.create_user(
            username='officer_test', email='officer@univ.ac.ke', password='Password123!'
        )
        UserRole.objects.create(user=self.officer_user, role=self.role_officer)

        # 6. Helper to create students and registrations
        self.students = []
        for i in range(1, 31): # 30 students
            u = User.objects.create_user(username=f'std_{i:02d}', email=f'std_{i:02d}@univ.ac.ke', password='Password123!')
            UserRole.objects.create(user=u, role=self.role_student)
            s = Student.objects.create(
                user=u,
                registration_number=f'CS/00{i:02d}/26',
                course=self.course,
                department=self.dept,
                school=self.school
            )
            self.students.append(s)


class ConstraintCheckerTests(SchedulingMilestone2BaseTestCase):
    """Tests for ExamConstraintChecker service enforcing all hard constraints."""

    def test_student_clash_detection_and_prevention(self):
        """Hard Constraint 1: Student cannot be scheduled for 2 overlapping exams."""
        exam_1 = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        exam_2 = Examination.objects.create(period=self.period, unit=self.unit_b, examination_type='FINAL')

        # Register students 0-9 in both Unit A and Unit B
        for s in self.students[:10]:
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')
            UnitRegistration.objects.create(student=s, unit=self.unit_b, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        # Schedule Exam 1
        ExamSchedule.objects.create(
            examination=exam_1,
            exam_date=date(2026, 10, 5),
            start_time=time(9, 0),
            end_time=time(12, 0),
            status='SCHEDULED'
        )

        # Attempt to check overlapping schedule for Exam 2 on the same day/time
        report = ExamConstraintChecker.check_student_clashes(
            examination=exam_2,
            exam_date=date(2026, 10, 5),
            start_time=time(10, 0), # Overlaps with 09:00 - 12:00
            end_time=time(13, 0)
        )

        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.hard_violations), 10)
        self.assertEqual(report.hard_violations[0].constraint_type, 'STUDENT_CLASH')
        self.assertIn("Student clash", report.hard_violations[0].message)

    def test_room_clash_detection_and_prevention(self):
        """Hard Constraint 2: A room cannot be double-booked for overlapping exams."""
        exam_1 = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        exam_2 = Examination.objects.create(period=self.period, unit=self.unit_b, examination_type='FINAL')

        ExamSchedule.objects.create(
            examination=exam_1,
            exam_date=date(2026, 10, 6),
            start_time=time(9, 0),
            end_time=time(12, 0),
            status='SCHEDULED'
        )
        ExamRoomAllocation.objects.create(
            examination=exam_1,
            room=self.room_50,
            allocated_capacity=30
        )

        # Check room clash for exam_2 attempting to use room_50 at overlapping time
        report = ExamConstraintChecker.check_room_clashes(
            room=self.room_50,
            exam_date=date(2026, 10, 6),
            start_time=time(11, 0), # Overlaps with 09:00 - 12:00
            end_time=time(14, 0)
        )

        self.assertFalse(report.is_valid)
        self.assertTrue(any(v.constraint_type == 'ROOM_CLASH' for v in report.hard_violations))
        self.assertIn("Room double-booking", report.hard_violations[0].message)

    def test_room_capacity_violation_prevention(self):
        """Hard Constraint 3: Room capacity cannot be exceeded."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')

        # Allocating 60 students to room_50 (capacity 50)
        allocations = [{'room': self.room_50, 'allocated_capacity': 60}]
        report = ExamConstraintChecker.check_room_capacity(exam, allocations)

        self.assertFalse(report.is_valid)
        self.assertTrue(any(v.constraint_type == 'ROOM_CAPACITY_EXCEEDED' for v in report.hard_violations))
        self.assertIn("capacity exceeded", report.hard_violations[0].message)

    def test_unregistered_student_rejected(self):
        """Hard Constraint 4: Cannot allocate a student without valid UnitRegistration."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')

        # Register only student 0
        UnitRegistration.objects.create(student=self.students[0], unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        # Attempt allocating student 0 (registered) and student 1 (unregistered)
        allocs = [
            {'student_id': self.students[0].id},
            {'student_id': self.students[1].id}
        ]
        report = ExamConstraintChecker.check_student_eligibility_and_duplication(exam, allocs)

        self.assertFalse(report.is_valid)
        self.assertTrue(any(v.constraint_type == 'UNREGISTERED_STUDENT' for v in report.hard_violations))
        self.assertIn("does not have a valid active registration", report.hard_violations[0].message)

    def test_duplicate_student_allocation_prevention(self):
        """Hard Constraint 8: Cannot allocate the same student twice for an examination."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        UnitRegistration.objects.create(student=self.students[0], unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        allocs = [
            {'student_id': self.students[0].id},
            {'student_id': self.students[0].id} # Duplicate!
        ]
        report = ExamConstraintChecker.check_student_eligibility_and_duplication(exam, allocs)

        self.assertFalse(report.is_valid)
        self.assertTrue(any(v.constraint_type == 'DUPLICATE_STUDENT_ALLOCATION' for v in report.hard_violations))

        # Database unique constraint verification
        StudentExamAllocation.objects.create(examination=exam, student=self.students[0], room=self.room_50)
        with self.assertRaises(IntegrityError):
            StudentExamAllocation.objects.create(examination=exam, student=self.students[0], room=self.room_40)

    def test_schedule_outside_period_dates_rejected(self):
        """Hard Constraint 5: Exam schedule date must fall within examination period."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')

        # Period is 2026-10-05 to 2026-10-16. Attempt 2026-10-25
        report = ExamConstraintChecker.check_time_boundaries(
            examination=exam,
            exam_date=date(2026, 10, 25),
            start_time=time(9, 0),
            end_time=time(12, 0)
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any(v.constraint_type == 'PERIOD_DATE_OUT_OF_BOUNDS' for v in report.hard_violations))

    def test_invalid_time_range_rejected(self):
        """Hard Constraint 6: Start time must precede end time."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')

        report = ExamConstraintChecker.check_time_boundaries(
            examination=exam,
            exam_date=date(2026, 10, 6),
            start_time=time(14, 0),
            end_time=time(11, 0) # End time before start time!
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any(v.constraint_type == 'INVALID_TIME_RANGE' for v in report.hard_violations))


class TimetableGenerationEngineTests(SchedulingMilestone2BaseTestCase):
    """Tests for TimetableSchedulerEngine algorithm, rollback, and soft constraints."""

    def test_successful_timetable_generation_multi_exam(self):
        """Engine schedules multiple exams into conflict-free slots and allocates rooms."""
        exam_1 = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        exam_2 = Examination.objects.create(period=self.period, unit=self.unit_b, examination_type='FINAL')
        exam_3 = Examination.objects.create(period=self.period, unit=self.unit_c, examination_type='FINAL')

        # Cohort 1: students 0-14 in Unit A
        for s in self.students[:15]:
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        # Cohort 2: students 10-24 in Unit B (shares students 10-14 with Unit A!)
        for s in self.students[10:25]:
            UnitRegistration.objects.create(student=s, unit=self.unit_b, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        # Cohort 3: students 20-29 in Unit C (shares students 20-24 with Unit B)
        for s in self.students[20:30]:
            UnitRegistration.objects.create(student=s, unit=self.unit_c, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        engine = TimetableSchedulerEngine(period_id=self.period.id)
        result = engine.execute_scheduling()

        self.assertTrue(result['success'])
        self.assertEqual(result['scheduled_count'], 3)

        # Audit entire generated timetable
        audit = ExamConstraintChecker.validate_period_entire_timetable(self.period)
        self.assertTrue(audit.is_valid)
        self.assertEqual(len(audit.hard_violations), 0)

        # Verify Exam A and Exam B are NOT scheduled in overlapping slots
        sched_a = ExamSchedule.objects.get(examination=exam_1)
        sched_b = ExamSchedule.objects.get(examination=exam_2)
        if sched_a.exam_date == sched_b.exam_date:
            self.assertTrue(sched_a.end_time <= sched_b.start_time or sched_b.end_time <= sched_a.start_time)

    def test_multi_room_cohort_split(self):
        """Large examination is cleanly partitioned across multiple rooms without duplicate allocation."""
        # Create a unit with 70 students (exceeds single largest room of 50)
        # Create additional students
        large_cohort = []
        for i in range(31, 101): # 70 students
            u = User.objects.create_user(username=f'std_lg_{i}', email=f'std_lg_{i}@univ.ac.ke', password='Password123!')
            UserRole.objects.create(user=u, role=self.role_student)
            s = Student.objects.create(user=u, registration_number=f'CS/LG/{i}/26', course=self.course, department=self.dept, school=self.school)
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')
            large_cohort.append(s)

        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')

        engine = TimetableSchedulerEngine(period_id=self.period.id)
        result = engine.execute_scheduling()

        self.assertTrue(result['success'])
        allocations = ExamRoomAllocation.objects.filter(examination=exam)
        self.assertGreater(allocations.count(), 1) # Must use at least 2 rooms (e.g. 50 + 20)

        total_cap = sum(a.allocated_capacity for a in allocations)
        self.assertEqual(total_cap, 70)

        # Check student seat allocations count
        student_seats = StudentExamAllocation.objects.filter(examination=exam)
        self.assertEqual(student_seats.count(), 70)

        # Verify no duplicate student allocations
        distinct_students = student_seats.values_list('student_id', flat=True).distinct().count()
        self.assertEqual(distinct_students, 70)

    def test_insufficient_room_capacity_triggers_rollback(self):
        """When student demand exceeds total university room capacity, engine rolls back with zero partial saves."""
        # Deactivate rooms so total capacity is only 30
        self.room_50.status = 'MAINTENANCE'
        self.room_50.save()
        self.room_40.status = 'MAINTENANCE'
        self.room_40.save()
        # Only room_30 remains (cap: 30)

        # Register 50 students for Unit A
        for s in self.students[:50]:
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')

        engine = TimetableSchedulerEngine(period_id=self.period.id)
        result = engine.execute_scheduling()

        # Must fail gracefully
        self.assertFalse(result['success'])
        self.assertGreater(len(result['report'].hard_violations), 0)

        # Verify transaction rollback: zero schedules or allocations exist in DB
        self.assertEqual(ExamSchedule.objects.filter(examination=exam).count(), 0)
        self.assertEqual(ExamRoomAllocation.objects.filter(examination=exam).count(), 0)
        self.assertEqual(StudentExamAllocation.objects.filter(examination=exam).count(), 0)

    def test_insufficient_time_slots_triggers_rollback(self):
        """When not enough clash-free slots exist for all exams, engine cleanly rolls back."""
        # Create a 1-day period with only 1 session slot
        short_period = ExaminationPeriod.objects.create(
            name='1-Day Period',
            academic_year='2026/2027',
            semester=1,
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 5), # Only Monday
            status='DRAFT'
        )

        exam_1 = Examination.objects.create(period=short_period, unit=self.unit_a, examination_type='FINAL')
        exam_2 = Examination.objects.create(period=short_period, unit=self.unit_b, examination_type='FINAL')

        # Mutually exclusive: 10 students registered for both units
        for s in self.students[:10]:
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=short_period.academic_year, semester=short_period.semester, registration_status='REGISTERED')
            UnitRegistration.objects.create(student=s, unit=self.unit_b, academic_year=short_period.academic_year, semester=short_period.semester, registration_status='REGISTERED')

        # Provide only 1 time slot (e.g. 09:00 - 12:00)
        engine = TimetableSchedulerEngine(
            period_id=short_period.id,
            time_slots=[(time(9, 0), time(12, 0))]
        )
        result = engine.execute_scheduling()

        # Both exams require the same single slot but clash on students -> Must fail & rollback
        self.assertFalse(result['success'])
        self.assertEqual(ExamSchedule.objects.filter(examination__period=short_period).count(), 0)


class PublicationAndWorkflowTests(SchedulingMilestone2BaseTestCase):
    """Tests for timetable validation and publication guards."""

    def test_publish_timetable_blocked_on_conflicts(self):
        """Timetable publication is blocked if any hard constraint conflicts exist."""
        exam_1 = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        exam_2 = Examination.objects.create(period=self.period, unit=self.unit_b, examination_type='FINAL')

        # Create overlapping schedules sharing students
        for s in self.students[:5]:
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')
            UnitRegistration.objects.create(student=s, unit=self.unit_b, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        ExamSchedule.objects.create(examination=exam_1, exam_date=date(2026, 10, 5), start_time=time(9, 0), end_time=time(12, 0))
        ExamSchedule.objects.create(examination=exam_2, exam_date=date(2026, 10, 5), start_time=time(9, 0), end_time=time(12, 0))

        success, report = ExamConstraintChecker.publish_timetable(self.period, self.officer_user)
        self.assertFalse(success)
        self.assertFalse(report.is_valid)
        self.assertEqual(self.period.status, 'DRAFT')

    def test_publish_timetable_succeeds_when_clean(self):
        """Clean timetable publishes successfully and updates schedule statuses."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        for s in self.students[:10]:
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        sched = ExamSchedule.objects.create(examination=exam, exam_date=date(2026, 10, 5), start_time=time(9, 0), end_time=time(12, 0))
        ExamRoomAllocation.objects.create(examination=exam, room=self.room_50, allocated_capacity=10)

        success, report = ExamConstraintChecker.publish_timetable(self.period, self.officer_user)
        self.assertTrue(success)
        self.assertTrue(report.is_valid)

        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'PUBLISHED')

        sched.refresh_from_db()
        self.assertEqual(sched.status, 'PUBLISHED')
        self.assertIsNotNone(sched.published_at)
        self.assertEqual(sched.published_by, self.officer_user)


class APISchedulingEndpointTests(SchedulingMilestone2BaseTestCase):
    """Tests DRF endpoints and role-based access control."""

    def test_generate_timetable_api_officer_success(self):
        """Examination officer can trigger timetable generation via REST API."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        for s in self.students[:10]:
            UnitRegistration.objects.create(student=s, unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        self.client.login(username='officer_test', password='Password123!')
        url = f'/api/scheduling/periods/{self.period.id}/generate-timetable/'
        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['scheduled_count'], 1)

    def test_student_cannot_generate_timetable(self):
        """Students are forbidden from invoking timetable generation endpoint."""
        self.client.login(username=self.students[0].user.username, password='Password123!')
        url = f'/api/scheduling/periods/{self.period.id}/generate-timetable/'
        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)

    def test_student_timetable_isolation(self):
        """Student endpoint only returns records allocated to the authenticated student."""
        exam = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        sched = ExamSchedule.objects.create(examination=exam, exam_date=date(2026, 10, 5), start_time=time(9, 0), end_time=time(12, 0))

        # Allocate only student 0
        StudentExamAllocation.objects.create(examination=exam, student=self.students[0], room=self.room_50)

        # Student 0 checks timetable -> sees 1 exam
        self.client.login(username=self.students[0].user.username, password='Password123!')
        resp_0 = self.client.get('/api/scheduling/my-timetable/')
        self.assertEqual(resp_0.status_code, 200)
        self.assertEqual(len(resp_0.data), 1)
        self.assertEqual(resp_0.data[0]['unit_code'], 'CSC401')

        # Student 1 checks timetable -> sees 0 exams
        self.client.login(username=self.students[1].user.username, password='Password123!')
        resp_1 = self.client.get('/api/scheduling/my-timetable/')
        self.assertEqual(resp_1.status_code, 200)
        self.assertEqual(len(resp_1.data), 0)

    def test_manual_schedule_api_blocks_student_clashes(self):
        """Manual POST to /api/scheduling/schedules/ rejects student clashes with 400."""
        exam_1 = Examination.objects.create(period=self.period, unit=self.unit_a, examination_type='FINAL')
        exam_2 = Examination.objects.create(period=self.period, unit=self.unit_b, examination_type='FINAL')

        # Shared student
        UnitRegistration.objects.create(student=self.students[0], unit=self.unit_a, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')
        UnitRegistration.objects.create(student=self.students[0], unit=self.unit_b, academic_year=self.period.academic_year, semester=self.period.semester, registration_status='REGISTERED')

        # Schedule exam_1
        ExamSchedule.objects.create(examination=exam_1, exam_date=date(2026, 10, 5), start_time=time(9, 0), end_time=time(12, 0))

        # Attempt to schedule exam_2 via API into overlapping slot
        self.client.login(username='officer_test', password='Password123!')
        payload = {
            'examination': exam_2.id,
            'exam_date': '2026-10-05',
            'start_time': '09:00',
            'end_time': '12:00',
            'status': 'SCHEDULED'
        }
        response = self.client.post('/api/scheduling/schedules/', data=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn('conflicts', response.data)
        self.assertTrue(any('Student clash' in err for err in response.data['conflicts']))
