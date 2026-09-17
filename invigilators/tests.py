from django.test import TestCase, Client
from django.contrib.auth import get_user_model
import json

from authentication.models import Role, UserRole
from academics.models import (
    School, Department, Course, Unit, Lecturer, Student,
    UnitRegistration
)
from locations.models import Campus, Building, Floor, Room
from scheduling.models import ExaminationPeriod, Examination, ExamRoomAllocation, StudentExamAllocation
from invigilators.models import InvigilatorDuty, ExamAttendance

User = get_user_model()


class AttendanceMarkingWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Roles
        self.lecturer_role, _ = Role.objects.get_or_create(name=Role.LECTURER)
        self.student_role, _ = Role.objects.get_or_create(name=Role.STUDENT)

        # Structure
        self.campus = Campus.objects.create(code='MAIN', name='Main Campus')
        self.building = Building.objects.create(campus=self.campus, code='ENG', name='Engineering Complex')
        self.floor = Floor.objects.create(building=self.building, floor_number=1, name='1st Floor')
        self.room = Room.objects.create(building=self.building, floor=self.floor, name='Exam Hall 1', room_number='EH-101', capacity=80)

        self.school = School.objects.create(code='SET', name='School of Engineering')
        self.dept = Department.objects.create(school=self.school, code='CE', name='Civil Engineering')
        self.course = Course.objects.create(department=self.dept, code='BSCCE', name='BSc Civil Engineering')
        self.unit = Unit.objects.create(course=self.course, code='CVE301', name='Structural Analysis', credit_hours=3.0)

        # Lecturer
        self.user_lec = User.objects.create_user(username='lecturer_cve', email='cve_lec@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_lec, role=self.lecturer_role)
        self.lecturer = Lecturer.objects.create(user=self.user_lec, staff_number='LEC-CVE-01', department=self.dept)

        # Exam Period & Exam
        self.period = ExaminationPeriod.objects.create(
            name='2026/2027 Semester 1 Exams',
            academic_year='2026/2027',
            semester=1,
            start_date='2026-10-01',
            end_date='2026-10-15',
            status='PUBLISHED'
        )
        self.examination = Examination.objects.create(
            period=self.period,
            unit=self.unit,
            examination_type='FINAL'
        )

        # Room Allocation
        self.room_alloc = ExamRoomAllocation.objects.create(
            examination=self.examination,
            room=self.room,
            allocated_capacity=80
        )

        # Students
        self.students = []
        for i in range(1, 4):
            u = User.objects.create_user(username=f'std_cve_{i}', email=f'std_cve_{i}@univ.ac.ke', password='Password123!')
            UserRole.objects.create(user=u, role=self.student_role)
            s = Student.objects.create(
                user=u,
                registration_number=f'CVE/00{i}/2023',
                course=self.course,
                department=self.dept,
                school=self.school
            )
            UnitRegistration.objects.create(
                student=s,
                unit=self.unit,
                academic_year=self.period.academic_year,
                semester=self.period.semester,
                registration_status='REGISTERED'
            )
            StudentExamAllocation.objects.create(
                examination=self.examination,
                student=s,
                room=self.room,
                seat_number=f'SEAT-{i:02d}'
            )
            self.students.append(s)

    def test_lecturer_open_exam_attendance_when_assigned(self):
        """Lecturer assigned to invigilate an exam room is redirected directly to the session roster."""
        duty = InvigilatorDuty.objects.create(
            examination=self.examination,
            lecturer=self.lecturer,
            room=self.room
        )
        self.client.login(username='lecturer_cve', password='Password123!')
        url = f'/results/lecturer/attendance/{self.examination.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/invigilator/session/{duty.id}/', response.url)

    def test_lecturer_open_exam_attendance_when_not_assigned_shows_warning(self):
        """Lecturer who is not assigned to invigilate an exam room receives a clear warning."""
        self.client.login(username='lecturer_cve', password='Password123!')
        url = f'/results/lecturer/attendance/{self.examination.id}/'
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        messages_text = [m.message for m in response.context['messages']]
        self.assertTrue(any("You are not assigned as an invigilator" in m for m in messages_text))


    def test_single_checkin_with_booklet_serial(self):
        """Single check-in saves the physical booklet serial number and marks student present."""
        duty = InvigilatorDuty.objects.create(
            examination=self.examination,
            lecturer=self.lecturer,
            room=self.room
        )
        self.client.login(username='lecturer_cve', password='Password123!')
        
        url = f'/invigilator/session/{duty.id}/checkin/'
        payload = {
            'student_id': self.students[0].id,
            'booklet_serial_number': 'BKT-CVE301-001',
            'is_present': True,
            'remarks': 'Seat register verified'
        }
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['status'], 'CHECKED_IN')
        self.assertEqual(data['booklet_serial_number'], 'BKT-CVE301-001')

        # Verify DB record
        att = ExamAttendance.objects.get(examination=self.examination, student=self.students[0])
        self.assertTrue(att.is_present)
        self.assertEqual(att.booklet_serial_number, 'BKT-CVE301-001')

    def test_checkin_rejects_empty_booklet_when_present(self):
        """Check-in requires physical booklet serial number if student is marked present."""
        duty = InvigilatorDuty.objects.create(
            examination=self.examination,
            lecturer=self.lecturer,
            room=self.room
        )
        self.client.login(username='lecturer_cve', password='Password123!')

        url = f'/invigilator/session/{duty.id}/checkin/'
        payload = {
            'student_id': self.students[0].id,
            'booklet_serial_number': '',
            'is_present': True
        }
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

    def test_checkin_marks_absent_cleanly(self):
        """Marking a student absent assigns unique absent booklet identifier."""
        duty = InvigilatorDuty.objects.create(
            examination=self.examination,
            lecturer=self.lecturer,
            room=self.room
        )
        self.client.login(username='lecturer_cve', password='Password123!')

        url = f'/invigilator/session/{duty.id}/checkin/'
        payload = {
            'student_id': self.students[1].id,
            'booklet_serial_number': '',
            'is_present': False
        }
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['status'], 'ABSENT')
        self.assertTrue(data['booklet_serial_number'].startswith('ABSENT-'))

    def test_batch_student_checkin(self):
        """Batch check-in updates multiple students with booklet serial numbers in one transaction."""
        duty = InvigilatorDuty.objects.create(
            examination=self.examination,
            lecturer=self.lecturer,
            room=self.room
        )
        self.client.login(username='lecturer_cve', password='Password123!')

        url = f'/invigilator/session/{duty.id}/batch-checkin/'
        payload = {
            'records': [
                {
                    'student_id': self.students[0].id,
                    'booklet_serial_number': 'BKT-BATCH-001',
                    'is_present': True
                },
                {
                    'student_id': self.students[1].id,
                    'booklet_serial_number': 'BKT-BATCH-002',
                    'is_present': True
                },
                {
                    'student_id': self.students[2].id,
                    'booklet_serial_number': '',
                    'is_present': False
                }
            ]
        }
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['updated_count'], 3)
        self.assertEqual(data['stats']['checked_in'], 2)
        self.assertEqual(data['stats']['absent'], 1)

    def test_batch_checkin_rejects_duplicate_booklet_in_batch(self):
        """Batch check-in warns/rejects duplicate booklet serial numbers submitted in the same batch."""
        duty = InvigilatorDuty.objects.create(
            examination=self.examination,
            lecturer=self.lecturer,
            room=self.room
        )
        self.client.login(username='lecturer_cve', password='Password123!')

        url = f'/invigilator/session/{duty.id}/batch-checkin/'
        payload = {
            'records': [
                {
                    'student_id': self.students[0].id,
                    'booklet_serial_number': 'BKT-DUP-999',
                    'is_present': True
                },
                {
                    'student_id': self.students[1].id,
                    'booklet_serial_number': 'BKT-DUP-999',  # Duplicate!
                    'is_present': True
                }
            ]
        }
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(any("Duplicate booklet" in err for err in data['errors']))


class LecturerMultiRoleArchitectureTests(TestCase):
    """
    Validates that:
    1. Lecturer is the core professional identity (User -> Lecturer Profile).
    2. Roles represent responsibilities (COD, DEAN held simultaneously with Lecturer).
    3. Invigilation is an examination room assignment (InvigilatorDuty), not a separate person or permanent user role.
    
    Person      Lecturer  Invigilator  COD   Dean
    Dr. A       ✅        ✅           ❌    ❌
    Dr. B       ✅        ✅           ✅    ❌
    Prof. C     ✅        ✅           ❌    ✅
    Dr. D       ✅        ❌           ❌    ❌
    """
    def setUp(self):
        self.client = Client()

        # Roles
        self.role_lecturer, _ = Role.objects.get_or_create(name=Role.LECTURER)
        self.role_cod, _ = Role.objects.get_or_create(name=Role.COD)
        self.role_dean, _ = Role.objects.get_or_create(name=Role.DEAN)

        # Academic Structure
        self.campus = Campus.objects.create(code='MAIN', name='Main Campus')
        self.building = Building.objects.create(campus=self.campus, code='SCI', name='Science Block')
        self.floor = Floor.objects.create(building=self.building, floor_number=1, name='1st Floor')
        self.room = Room.objects.create(building=self.building, floor=self.floor, name='Room 101', room_number='R101', capacity=50)

        self.school = School.objects.create(code='SIST', name='School of Information Sciences')
        self.dept = Department.objects.create(school=self.school, code='CS', name='Computer Science')
        self.course = Course.objects.create(department=self.dept, code='BSCCS', name='BSc Computer Science')
        self.unit = Unit.objects.create(course=self.course, code='CSC301', name='Operating Systems', credit_hours=3.0)

        self.period = ExaminationPeriod.objects.create(
            name='2026/2027 Main Exams',
            academic_year='2026/2027',
            semester=1,
            start_date='2026-10-01',
            end_date='2026-10-20',
            status='PUBLISHED'
        )
        self.exam = Examination.objects.create(
            period=self.period,
            unit=self.unit,
            examination_type='FINAL'
        )

        # Dr. A: Lecturer + Invigilator Duty
        self.user_a = User.objects.create_user(username='dr_a', email='dr_a@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_a, role=self.role_lecturer)
        self.lec_a = Lecturer.objects.create(user=self.user_a, staff_number='LEC-001', department=self.dept)

        # Dr. B: Lecturer + COD + Invigilator Duty
        self.user_b = User.objects.create_user(username='dr_b', email='dr_b@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_b, role=self.role_lecturer)
        UserRole.objects.create(user=self.user_b, role=self.role_cod)
        self.lec_b = Lecturer.objects.create(user=self.user_b, staff_number='LEC-002', department=self.dept)

        # Prof. C: Lecturer + Dean + Invigilator Duty
        self.user_c = User.objects.create_user(username='prof_c', email='prof_c@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_c, role=self.role_lecturer)
        UserRole.objects.create(user=self.user_c, role=self.role_dean)
        self.lec_c = Lecturer.objects.create(user=self.user_c, staff_number='LEC-003', department=self.dept)

        # Dr. D: Lecturer only (No invigilation duties, no COD, no Dean)
        self.user_d = User.objects.create_user(username='dr_d', email='dr_d@univ.ac.ke', password='Password123!')
        UserRole.objects.create(user=self.user_d, role=self.role_lecturer)
        self.lec_d = Lecturer.objects.create(user=self.user_d, staff_number='LEC-004', department=self.dept)

        # Assign room duties to Dr. A, Dr. B, and Prof. C
        self.duty_a = InvigilatorDuty.objects.create(examination=self.exam, lecturer=self.lec_a, room=self.room, role='CHIEF_INVIGILATOR')
        self.duty_b = InvigilatorDuty.objects.create(examination=self.exam, lecturer=self.lec_b, room=self.room, role='INVIGILATOR')
        # Prof. C assigned to another examination or room
        self.room_2 = Room.objects.create(building=self.building, floor=self.floor, name='Room 102', room_number='R102', capacity=50)
        self.duty_c = InvigilatorDuty.objects.create(examination=self.exam, lecturer=self.lec_c, room=self.room_2, role='INVIGILATOR')

    def test_dr_a_lecturer_and_invigilator(self):
        """Dr. A can access teaching marks and invigilation duties, but not COD or Dean dashboards."""
        self.assertTrue(self.user_a.is_lecturer)
        self.assertTrue(self.user_a.has_invigilation_duties)
        self.assertFalse(self.user_a.is_cod)
        self.assertFalse(self.user_a.is_dean)

        self.client.login(username='dr_a', password='Password123!')

        # Can access marks entry dashboard
        resp = self.client.get('/results/lecturer/')
        self.assertEqual(resp.status_code, 200)

        # Can access invigilator duties dashboard
        resp = self.client.get('/invigilator/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['duties_list']), 1)

        # Cannot access COD or Dean review dashboards
        resp_cod = self.client.get('/results/cod/')
        self.assertEqual(resp_cod.status_code, 403)
        resp_dean = self.client.get('/results/dean/')
        self.assertEqual(resp_dean.status_code, 403)

    def test_dr_b_lecturer_cod_and_invigilator(self):
        """Dr. B can teach/enter marks, invigilate assigned rooms, and access the COD dashboard."""
        self.assertTrue(self.user_b.is_lecturer)
        self.assertTrue(self.user_b.has_invigilation_duties)
        self.assertTrue(self.user_b.is_cod)
        self.assertFalse(self.user_b.is_dean)

        self.client.login(username='dr_b', password='Password123!')

        # 1. Access lecturer marks entry for units they teach
        resp_lec = self.client.get('/results/lecturer/')
        self.assertEqual(resp_lec.status_code, 200)

        # 2. Access invigilation room dashboard
        resp_inv = self.client.get('/invigilator/dashboard/')
        self.assertEqual(resp_inv.status_code, 200)
        self.assertEqual(len(resp_inv.context['duties_list']), 1)

        # 3. Access COD departmental results review
        resp_cod = self.client.get('/results/cod/')
        self.assertEqual(resp_cod.status_code, 200)

        # 4. Cannot access Dean dashboard
        resp_dean = self.client.get('/results/dean/')
        self.assertEqual(resp_dean.status_code, 403)

    def test_prof_c_lecturer_dean_and_invigilator(self):
        """Prof. C can teach/enter marks, invigilate assigned rooms, and access the Dean dashboard."""
        self.assertTrue(self.user_c.is_lecturer)
        self.assertTrue(self.user_c.has_invigilation_duties)
        self.assertFalse(self.user_c.is_cod)
        self.assertTrue(self.user_c.is_dean)

        self.client.login(username='prof_c', password='Password123!')

        # 1. Access lecturer marks entry
        resp_lec = self.client.get('/results/lecturer/')
        self.assertEqual(resp_lec.status_code, 200)

        # 2. Access invigilation room dashboard
        resp_inv = self.client.get('/invigilator/dashboard/')
        self.assertEqual(resp_inv.status_code, 200)
        self.assertEqual(len(resp_inv.context['duties_list']), 1)

        # 3. Access Dean faculty results review
        resp_dean = self.client.get('/results/dean/')
        self.assertEqual(resp_dean.status_code, 200)

        # 4. Cannot access COD dashboard
        resp_cod = self.client.get('/results/cod/')
        self.assertEqual(resp_cod.status_code, 403)

    def test_dr_d_lecturer_only_without_invigilation_duty(self):
        """Dr. D is a lecturer only: can enter marks, has 0 invigilator duties, cannot COD/Dean."""
        self.assertTrue(self.user_d.is_lecturer)
        self.assertFalse(self.user_d.has_invigilation_duties)
        self.assertFalse(self.user_d.is_cod)
        self.assertFalse(self.user_d.is_dean)

        self.client.login(username='dr_d', password='Password123!')

        # 1. Access lecturer marks entry
        resp_lec = self.client.get('/results/lecturer/')
        self.assertEqual(resp_lec.status_code, 200)

        # 2. Invigilator dashboard reports 0 duties
        resp_inv = self.client.get('/invigilator/dashboard/')
        self.assertEqual(resp_inv.status_code, 200)
        self.assertEqual(len(resp_inv.context['duties_list']), 0)

        # 3. Cannot access COD or Dean dashboards
        self.assertEqual(self.client.get('/results/cod/').status_code, 403)
        self.assertEqual(self.client.get('/results/dean/').status_code, 403)
