import io
import csv
from decimal import Decimal
from django.test import TestCase, Client
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from authentication.models import Role, UserRole
from academics.models import (
    School, Department, Course, Unit, Lecturer, Student,
    UnitRegistration, StudentMark, ResultSubmission, ResultWorkflowAudit
)
from locations.models import Campus, Building, Floor, Room
from scheduling.models import ExaminationPeriod, Examination
from invigilators.models import ExamAttendance
from academics.results_services import BulkMarkUploadService, ResultWorkflowService

User = get_user_model()


class Milestone8ResultsWorkflowTests(TestCase):
    def setUp(self):
        # 1. Setup Roles
        self.admin_role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.exam_officer_role, _ = Role.objects.get_or_create(name=Role.EXAM_OFFICER)
        self.dean_role, _ = Role.objects.get_or_create(name=Role.DEAN)
        self.cod_role, _ = Role.objects.get_or_create(name=Role.COD)
        self.lecturer_role, _ = Role.objects.get_or_create(name=Role.LECTURER)
        self.student_role, _ = Role.objects.get_or_create(name=Role.STUDENT)

        # 2. Location
        self.campus = Campus.objects.create(code='MAIN', name='Main Campus')
        self.building = Building.objects.create(campus=self.campus, code='SC', name='Science Complex')
        self.floor = Floor.objects.create(building=self.building, floor_number=0, name='Ground Floor')
        self.room = Room.objects.create(building=self.building, floor=self.floor, name='Hall A', room_number='SC-001', capacity=100)

        # 3. Academic Structure (2 Schools & Departments for RBAC isolation testing)
        self.school_cs = School.objects.create(code='SIST', name='School of Information Science')
        self.dept_cs = Department.objects.create(school=self.school_cs, code='CS', name='Computer Science')

        self.school_biz = School.objects.create(code='SOB', name='School of Business')
        self.dept_biz = Department.objects.create(school=self.school_biz, code='FIN', name='Finance')

        self.course_cs = Course.objects.create(department=self.dept_cs, code='BSCCS', name='BSc Computer Science')
        self.course_biz = Course.objects.create(department=self.dept_biz, code='BCOM', name='Bachelor of Commerce')

        self.unit_cs = Unit.objects.create(course=self.course_cs, code='CSC401', name='Software Architecture', credit_hours=3.0)
        self.unit_biz = Unit.objects.create(course=self.course_biz, code='FIN401', name='Corporate Finance', credit_hours=3.0)

        # 4. Users & Profiles
        # Lecturer 1 (CS)
        self.user_lec = User.objects.create_user(username='lec001', email='lec001@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_lec, role=self.lecturer_role)
        self.lecturer_cs = Lecturer.objects.create(user=self.user_lec, staff_number='EMP001', department=self.dept_cs)

        # COD (CS)
        self.user_cod_cs = User.objects.create_user(username='cod_cs', email='cod_cs@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_cod_cs, role=self.cod_role)
        self.lecturer_cod_cs = Lecturer.objects.create(user=self.user_cod_cs, staff_number='EMP002', department=self.dept_cs)

        # COD (Business)
        self.user_cod_biz = User.objects.create_user(username='cod_biz', email='cod_biz@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_cod_biz, role=self.cod_role)
        self.lecturer_cod_biz = Lecturer.objects.create(user=self.user_cod_biz, staff_number='EMP003', department=self.dept_biz)

        # Dean (SIST)
        self.user_dean_cs = User.objects.create_user(username='dean_sist', email='dean@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_dean_cs, role=self.dean_role)
        self.lecturer_dean_cs = Lecturer.objects.create(user=self.user_dean_cs, staff_number='EMP004', department=self.dept_cs)

        # Dean (SOB)
        self.user_dean_biz = User.objects.create_user(username='dean_sob', email='dean_biz@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_dean_biz, role=self.dean_role)
        self.lecturer_dean_biz = Lecturer.objects.create(user=self.user_dean_biz, staff_number='EMP005', department=self.dept_biz)

        # Exam Officer
        self.user_officer = User.objects.create_user(username='officer01', email='officer@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_officer, role=self.exam_officer_role)

        # Students (2 Students in CS)
        self.user_std1 = User.objects.create_user(username='std001', email='std001@univ.ac.ke', password='Password123!', first_name='John', last_name='Doe')
        UserRole.objects.create(user=self.user_std1, role=self.student_role)
        self.student1 = Student.objects.create(
            user=self.user_std1, registration_number='CT101/0001/26', course=self.course_cs, department=self.dept_cs, school=self.school_cs
        )

        self.user_std2 = User.objects.create_user(username='std002', email='std002@univ.ac.ke', password='Password123!', first_name='Jane', last_name='Smith')
        UserRole.objects.create(user=self.user_std2, role=self.student_role)
        self.student2 = Student.objects.create(
            user=self.user_std2, registration_number='CT101/0002/26', course=self.course_cs, department=self.dept_cs, school=self.school_cs
        )

        # 5. Examination Period & Examinations
        self.period = ExaminationPeriod.objects.create(
            name='2026/2027 Semester 1 Exams', academic_year='2026/2027', semester=1,
            start_date='2026-10-01', end_date='2026-10-15', status='PUBLISHED'
        )

        self.exam_cs = Examination.objects.create(
            period=self.period, unit=self.unit_cs, examination_type='FINAL', status='SCHEDULED'
        )

        # 6. Unit Registrations
        UnitRegistration.objects.create(
            student=self.student1, unit=self.unit_cs, academic_year='2026/2027', semester=1, registration_status='REGISTERED'
        )
        UnitRegistration.objects.create(
            student=self.student2, unit=self.unit_cs, academic_year='2026/2027', semester=1, registration_status='REGISTERED'
        )

        # 7. Live Attendance with physical booklets
        ExamAttendance.objects.create(
            examination=self.exam_cs, student=self.student1, room=self.room, recorded_by=self.lecturer_cs,
            booklet_serial_number='BKT-CSC401-001', is_present=True
        )
        ExamAttendance.objects.create(
            examination=self.exam_cs, student=self.student2, room=self.room, recorded_by=self.lecturer_cs,
            booklet_serial_number='BKT-CSC401-002', is_present=True
        )

        self.client = Client()

    # -------------------------------------------------------------
    # 1. Model & Grade Calculation Tests
    # -------------------------------------------------------------
    def test_student_mark_grade_calculation(self):
        mark_a = StudentMark(student=self.student1, examination=self.exam_cs, coursework_mark=30, exam_mark=50)
        mark_a.save()
        self.assertEqual(mark_a.total_mark, Decimal('80.00'))
        self.assertEqual(mark_a.grade, 'A')

        mark_b = StudentMark(student=self.student2, examination=self.exam_cs, coursework_mark=20, exam_mark=45)
        mark_b.save()
        self.assertEqual(mark_b.total_mark, Decimal('65.00'))
        self.assertEqual(mark_b.grade, 'B')

    def test_result_submission_initialization(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)
        self.assertEqual(sub.status, 'DRAFT')
        self.assertEqual(sub.unit, self.unit_cs)
        self.assertEqual(sub.department, self.dept_cs)
        self.assertEqual(sub.school, self.school_cs)
        # Check audit trail initialized
        self.assertTrue(sub.audit_trail.filter(action='CREATED').exists())

    # -------------------------------------------------------------
    # 2. Bulk Upload Validation & Atomic Rollback Tests
    # -------------------------------------------------------------
    def test_valid_csv_bulk_upload(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)

        csv_content = (
            "registration_number,cat_mark,exam_mark\n"
            "CT101/0001/26,28,55\n"
            "CT101/0002/26,22,48\n"
        ).encode('utf-8')
        uploaded_file = SimpleUploadedFile("marks.csv", csv_content, content_type="text/csv")

        result = BulkMarkUploadService.process_file(uploaded_file, sub, self.user_lec)
        self.assertTrue(result['success'])
        self.assertEqual(result['processed_count'], 2)

        # Verify marks saved in DB
        mark1 = StudentMark.objects.get(student=self.student1, examination=self.exam_cs)
        self.assertEqual(mark1.total_mark, Decimal('83.00'))
        self.assertEqual(mark1.grade, 'A')

        mark2 = StudentMark.objects.get(student=self.student2, examination=self.exam_cs)
        self.assertEqual(mark2.total_mark, Decimal('70.00'))
        self.assertEqual(mark2.grade, 'A')

        # Verify audit log
        self.assertTrue(sub.audit_trail.filter(action='BULK_UPLOAD').exists())

    def test_invalid_csv_atomic_rollback_on_unregistered_student(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)

        # Create unregistered student
        user_unreg = User.objects.create_user(username='std999', email='unreg@univ.ac.ke', password='Password123!')
        Student.objects.create(
            user=user_unreg, registration_number='CT101/9999/26', course=self.course_cs, department=self.dept_cs, school=self.school_cs
        )

        csv_content = (
            "registration_number,cat_mark,exam_mark\n"
            "CT101/0001/26,30,60\n"
            "CT101/9999/26,25,50\n" # Unregistered for CSC401
        ).encode('utf-8')
        uploaded_file = SimpleUploadedFile("marks.csv", csv_content, content_type="text/csv")

        result = BulkMarkUploadService.process_file(uploaded_file, sub, self.user_lec)
        self.assertFalse(result['success'])
        self.assertTrue(any("not registered" in err for err in result['errors']))

        # Atomic check: ensure CT101/0001/26 was NOT saved
        self.assertFalse(StudentMark.objects.filter(student=self.student1, examination=self.exam_cs).exists())

    def test_invalid_csv_mark_range_exceeded(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)

        csv_content = (
            "registration_number,cat_mark,exam_mark\n"
            "CT101/0001/26,45,60\n" # CAT 45 exceeds max 40
        ).encode('utf-8')
        uploaded_file = SimpleUploadedFile("marks.csv", csv_content, content_type="text/csv")

        result = BulkMarkUploadService.process_file(uploaded_file, sub, self.user_lec)
        self.assertFalse(result['success'])
        self.assertTrue(any("out of range" in err for err in result['errors']))

    def test_duplicate_student_in_file_detection(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)

        csv_content = (
            "registration_number,cat_mark,exam_mark\n"
            "CT101/0001/26,25,50\n"
            "CT101/0001/26,28,52\n" # Duplicate row
        ).encode('utf-8')
        uploaded_file = SimpleUploadedFile("marks.csv", csv_content, content_type="text/csv")

        result = BulkMarkUploadService.process_file(uploaded_file, sub, self.user_lec)
        self.assertFalse(result['success'])
        self.assertTrue(any("Duplicate registration number" in err for err in result['errors']))

    # -------------------------------------------------------------
    # 3. State Machine & Full Workflow Lifecycle Tests
    # -------------------------------------------------------------
    def test_full_approval_and_publication_workflow(self):
        # 1. Lecturer enters marks
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)
        ResultWorkflowService.save_manual_marks(sub, self.user_lec, [
            {'student_id': self.student1.id, 'coursework_mark': 25, 'exam_mark': 55},
            {'student_id': self.student2.id, 'coursework_mark': 30, 'exam_mark': 60},
        ])
        self.assertEqual(sub.status, 'DRAFT')

        # 2. Lecturer submits to COD
        ResultWorkflowService.submit_to_cod(sub, self.user_lec, comments="Draft verified by examiner.")
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'SUBMITTED')
        self.assertIsNotNone(sub.submitted_at)

        # 3. COD approves
        ResultWorkflowService.cod_review(sub, self.user_cod_cs, action='APPROVE', comments="Reviewed and endorsed.")
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'COD_APPROVED')
        self.assertEqual(sub.cod_reviewed_by, self.user_cod_cs)

        # 4. Dean approves
        ResultWorkflowService.dean_review(sub, self.user_dean_cs, action='APPROVE', comments="School board approval confirmed.")
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'DEAN_APPROVED')
        self.assertEqual(sub.dean_reviewed_by, self.user_dean_cs)

        # 5. Examination Officer publishes
        ResultWorkflowService.final_review_and_publish(sub, self.user_officer, action='PUBLISH', comments="Official publication.")
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'PUBLISHED')
        self.assertEqual(sub.published_by, self.user_officer)

        # 6. Verify marks status changed to PUBLISHED
        mark1 = StudentMark.objects.get(student=self.student1, examination=self.exam_cs)
        self.assertEqual(mark1.status, 'PUBLISHED')

    def test_cod_rejection_workflow(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)
        ResultWorkflowService.save_manual_marks(sub, self.user_lec, [
            {'student_id': self.student1.id, 'coursework_mark': 20, 'exam_mark': 40},
            {'student_id': self.student2.id, 'coursework_mark': 25, 'exam_mark': 45},
        ])
        ResultWorkflowService.submit_to_cod(sub, self.user_lec)

        # COD rejects
        ResultWorkflowService.cod_review(
            sub, self.user_cod_cs, action='REJECT', rejection_reason="Coursework marks missing component."
        )
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'COD_REJECTED')
        self.assertEqual(sub.rejection_reason, "Coursework marks missing component.")

        # Lecturer corrects and resubmits
        ResultWorkflowService.save_manual_marks(sub, self.user_lec, [
            {'student_id': self.student1.id, 'coursework_mark': 28, 'exam_mark': 40},
            {'student_id': self.student2.id, 'coursework_mark': 25, 'exam_mark': 45},
        ])
        ResultWorkflowService.submit_to_cod(sub, self.user_lec, comments="Resubmitted after coursework correction.")
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'SUBMITTED')

    def test_invalid_state_transitions_blocked(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)
        # Attempting to publish directly from DRAFT must fail
        with self.assertRaises(ValidationError):
            ResultWorkflowService.final_review_and_publish(sub, self.user_officer, action='PUBLISH')

        # Attempting Dean review from DRAFT must fail
        with self.assertRaises(ValidationError):
            ResultWorkflowService.dean_review(sub, self.user_dean_cs, action='APPROVE')

    def test_reconciliation_gating_blocks_on_ghost_marks(self):
        # Remove attendance for student 2 so submitting mark for student 2 generates GHOST_MARKS
        ExamAttendance.objects.filter(examination=self.exam_cs, student=self.student2).delete()

        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)
        ResultWorkflowService.save_manual_marks(sub, self.user_lec, [
            {'student_id': self.student1.id, 'coursework_mark': 25, 'exam_mark': 55},
            {'student_id': self.student2.id, 'coursework_mark': 30, 'exam_mark': 60},
        ])

        with self.assertRaises(ValidationError) as ctx:
            ResultWorkflowService.submit_to_cod(sub, self.user_lec)
        self.assertIn("Critical reconciliation anomalies detected", str(ctx.exception))

    # -------------------------------------------------------------
    # 4. RBAC & Scoped Permissions Tests
    # -------------------------------------------------------------
    def test_cod_cannot_approve_other_department_results(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)
        ResultWorkflowService.save_manual_marks(sub, self.user_lec, [
            {'student_id': self.student1.id, 'coursework_mark': 20, 'exam_mark': 40},
            {'student_id': self.student2.id, 'coursework_mark': 25, 'exam_mark': 45},
        ])
        ResultWorkflowService.submit_to_cod(sub, self.user_lec)

        # Business COD attempts to approve CS results
        with self.assertRaises(ValidationError) as ctx:
            ResultWorkflowService.cod_review(sub, self.user_cod_biz, action='APPROVE')
        self.assertIn("Permission Denied", str(ctx.exception))

    def test_dean_cannot_approve_other_school_results(self):
        sub = ResultWorkflowService.get_or_create_submission(self.exam_cs, self.lecturer_cs)
        ResultWorkflowService.save_manual_marks(sub, self.user_lec, [
            {'student_id': self.student1.id, 'coursework_mark': 20, 'exam_mark': 40},
            {'student_id': self.student2.id, 'coursework_mark': 25, 'exam_mark': 45},
        ])
        ResultWorkflowService.submit_to_cod(sub, self.user_lec)
        ResultWorkflowService.cod_review(sub, self.user_cod_cs, action='APPROVE')

        # Business Dean attempts to approve SIST School results
        with self.assertRaises(ValidationError) as ctx:
            ResultWorkflowService.dean_review(sub, self.user_dean_biz, action='APPROVE')
        self.assertIn("Permission Denied", str(ctx.exception))

    def test_student_portal_displays_only_published_marks(self):
        # Create 1 DRAFT mark and 1 PUBLISHED mark
        mark_draft = StudentMark.objects.create(
            student=self.student1, examination=self.exam_cs, coursework_mark=20, exam_mark=40, status='DRAFT'
        )

        self.client.login(username='std001', password='Password123!')
        resp = self.client.get('/student/results/')
        self.assertEqual(resp.status_code, 200)
        # Should not see draft marks in table
        self.assertNotContains(resp, "CSC401")

        # Now update mark to PUBLISHED
        mark_draft.status = 'PUBLISHED'
        mark_draft.save()

        resp2 = self.client.get('/student/results/')
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, "CSC401")
        self.assertContains(resp2, "Software Architecture")
        self.assertContains(resp2, "60.00")
