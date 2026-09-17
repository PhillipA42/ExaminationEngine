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

    def test_lecturer_open_exam_attendance_auto_provisions_duty(self):
        """Lecturer opening attendance marking for their exam automatically provisions duty and redirects."""
        self.client.login(username='lecturer_cve', password='Password123!')
        url = f'/results/lecturer/attendance/{self.examination.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        # Duty should now exist
        duty = InvigilatorDuty.objects.filter(examination=self.examination, lecturer=self.lecturer).first()
        self.assertIsNotNone(duty)
        self.assertEqual(duty.room, self.room)
        self.assertIn(f'/invigilator/session/{duty.id}/', response.url)

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
