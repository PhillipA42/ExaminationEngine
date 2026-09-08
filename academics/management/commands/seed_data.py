from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from authentication.models import Role, UserRole
from academics.models import (
    School, Department, Course, Unit, Student, UnitRegistration, Lecturer,
    StudentMark
)
from locations.models import Campus, Building, Floor, Room
from scheduling.models import ExaminationPeriod, Examination, ExamSchedule, ExamRoomAllocation, StudentExamAllocation
from scheduling.engine import TimetableSchedulerEngine
from invigilators.models import InvigilatorDuty, ExamAttendance
from malpractice.models import MalpracticeCase, MalpracticeEvidence

User = get_user_model()

class Command(BaseCommand):
    help = 'Seeds database with realistic test data including lecturers, duties, attendance with booklets, malpractice cases, and assessment marks.'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Seeding database with comprehensive test data...'))

        # -------------------------------------------------------------
        # 1. ROLES
        # -------------------------------------------------------------
        student_role, _ = Role.objects.get_or_create(name=Role.STUDENT, defaults={'description': 'Student Role'})
        lecturer_role, _ = Role.objects.get_or_create(name=Role.LECTURER, defaults={'description': 'Lecturer Role'})
        cod_role, _ = Role.objects.get_or_create(name=Role.COD, defaults={'description': 'Chairman of Department'})
        dean_role, _ = Role.objects.get_or_create(name=Role.DEAN, defaults={'description': 'Dean of School'})
        exam_officer_role, _ = Role.objects.get_or_create(name=Role.EXAM_OFFICER, defaults={'description': 'Examination Officer / Timetabler'})
        admin_role, _ = Role.objects.get_or_create(name=Role.ADMIN, defaults={'description': 'System Administrator'})

        # -------------------------------------------------------------
        # 2. LOCATIONS (Campus, Building, Floor, Rooms)
        # -------------------------------------------------------------
        campus, _ = Campus.objects.get_or_create(
            code='MAIN',
            defaults={
                'name': 'Main Campus',
                'description': 'Primary University Campus',
                'latitude': -0.678120,
                'longitude': 34.773120
            }
        )

        building, _ = Building.objects.get_or_create(
            campus=campus,
            code='SC',
            defaults={
                'name': 'Science Complex',
                'description': 'Faculty of Science & Computing',
                'latitude': -0.678500,
                'longitude': 34.773500
            }
        )

        floor_ground, _ = Floor.objects.get_or_create(
            building=building,
            floor_number=0,
            defaults={'name': 'Ground Floor'}
        )

        floor_first, _ = Floor.objects.get_or_create(
            building=building,
            floor_number=1,
            defaults={'name': 'First Floor'}
        )

        room_a, _ = Room.objects.get_or_create(
            building=building,
            floor=floor_ground,
            name='Hall A (SC-001)',
            defaults={'room_number': 'SC-001', 'capacity': 100, 'room_type': 'AUDITORIUM'}
        )

        room_b, _ = Room.objects.get_or_create(
            building=building,
            floor=floor_first,
            name='Room SC-101',
            defaults={'room_number': 'SC-101', 'capacity': 50, 'room_type': 'LECTURE_HALL'}
        )

        # -------------------------------------------------------------
        # 3. ACADEMIC HIERARCHY (School, Department, Course, Units)
        # -------------------------------------------------------------
        school, _ = School.objects.get_or_create(
            code='SIST',
            defaults={'name': 'School of Information Science and Technology'}
        )

        department, _ = Department.objects.get_or_create(
            school=school,
            code='CS',
            defaults={'name': 'Department of Computer Science'}
        )

        course, _ = Course.objects.get_or_create(
            department=department,
            code='BSCCS',
            defaults={'name': 'BSc Computer Science', 'duration': 4}
        )

        # Create 4 Units
        units_data = [
            ('CSC401', 'Software Architecture & Design', 1, 1),
            ('CSC402', 'Database Administration', 1, 1),
            ('CSC403', 'Constraint Optimization Engine Methods', 1, 1),
            ('MAT401', 'Discrete Mathematics II', 1, 1),
        ]

        units = []
        for code, name, year, sem in units_data:
            u, _ = Unit.objects.get_or_create(
                course=course,
                code=code,
                defaults={'name': name, 'year_of_study': year, 'semester': sem}
            )
            units.append(u)

        # -------------------------------------------------------------
        # 4. LECTURERS & TEST ACCOUNTS
        # -------------------------------------------------------------
        lecturers_data = [
            ('lec001', 'lec001@university.ac.ke', 'EMP-CS-001', 'Alan', 'Turing', 'Senior Lecturer'),
            ('lec002', 'lec002@university.ac.ke', 'EMP-CS-002', 'Grace', 'Hopper', 'Professor'),
            ('lec003', 'lec003@university.ac.ke', 'EMP-CS-003', 'Donald', 'Knuth', 'Associate Professor'),
        ]

        lecturers = []
        for username, email, staff_no, first_name, last_name, designation in lecturers_data:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': email,
                    'first_name': first_name,
                    'last_name': last_name,
                }
            )
            if created:
                user.set_password('Password123!')
                user.save()
                UserRole.objects.create(user=user, role=lecturer_role)

            lecturer_profile, _ = Lecturer.objects.get_or_create(
                user=user,
                defaults={
                    'staff_number': staff_no,
                    'department': department,
                    'designation': designation,
                    'status': 'ACTIVE'
                }
            )
            lecturers.append(lecturer_profile)

        # Assign COD role to Grace Hopper (lec002) and Dean to Donald Knuth (lec003)
        UserRole.objects.get_or_create(user=User.objects.get(username='lec002'), role=cod_role)
        UserRole.objects.get_or_create(user=User.objects.get(username='lec003'), role=dean_role)

        # Create dedicated Examination Officer account
        officer_user, created = User.objects.get_or_create(
            username='officer001',
            defaults={
                'email': 'officer001@university.ac.ke',
                'first_name': 'Tim',
                'last_name': 'Berners-Lee',
                'is_staff': True
            }
        )
        if created:
            officer_user.set_password('Password123!')
            officer_user.save()
            UserRole.objects.create(user=officer_user, role=exam_officer_role)

        # -------------------------------------------------------------
        # 5. STUDENTS & UNIT REGISTRATION
        # -------------------------------------------------------------
        students_data = [
            ('std001', 'std001@university.ac.ke', 'CT101/0001/26', 'John', 'Doe'),
            ('std002', 'std002@university.ac.ke', 'CT101/0002/26', 'Jane', 'Smith'),
            ('std003', 'std003@university.ac.ke', 'CT101/0003/26', 'Alex', 'Kipchoge'),
            ('std004', 'std004@university.ac.ke', 'CT101/0004/26', 'Mary', 'Wanjiku'),
            ('std005', 'std005@university.ac.ke', 'CT101/0005/26', 'Brian', 'Ochieng'),
        ]

        students = []
        for username, email, reg_no, first_name, last_name in students_data:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': email,
                    'first_name': first_name,
                    'last_name': last_name,
                }
            )
            if created:
                user.set_password('Password123!')
                user.save()
                UserRole.objects.create(user=user, role=student_role)

            student_profile, _ = Student.objects.get_or_create(
                user=user,
                defaults={
                    'registration_number': reg_no,
                    'course': course,
                    'department': department,
                    'school': school,
                    'year_of_study': 4
                }
            )
            students.append(student_profile)

            # Register student for all 4 units
            for unit in units:
                UnitRegistration.objects.get_or_create(
                    student=student_profile,
                    unit=unit,
                    academic_year='2026/2027',
                    semester=1,
                    defaults={'registration_status': 'REGISTERED'}
                )

        # -------------------------------------------------------------
        # 6. EXAMINATION PERIOD & EXAMINATIONS
        # -------------------------------------------------------------
        start_date = date.today() + timedelta(days=7)
        end_date = start_date + timedelta(days=14)

        period, _ = ExaminationPeriod.objects.get_or_create(
            name='2026/2027 Semester 1 Main Examinations',
            defaults={
                'academic_year': '2026/2027',
                'semester': 1,
                'start_date': start_date,
                'end_date': end_date,
                'status': 'DRAFT'
            }
        )

        examinations = []
        for unit in units:
            exam, _ = Examination.objects.get_or_create(
                period=period,
                unit=unit,
                examination_type='FINAL',
                defaults={
                    'duration_minutes': 180,
                    'status': 'PENDING'
                }
            )
            examinations.append(exam)

        # -------------------------------------------------------------
        # 7. TIMETABLE GENERATION & SCHEDULING
        # -------------------------------------------------------------
        scheduler = TimetableSchedulerEngine(period.id)
        scheduler.generate_timetable()

        # -------------------------------------------------------------
        # 8. INVIGILATOR DUTY ASSIGNMENTS
        # -------------------------------------------------------------
        # Assign invigilator duties for each scheduled exam
        for i, exam in enumerate(examinations):
            allocated_rooms = Room.objects.filter(allocated_exams__examination=exam)
            assigned_room = allocated_rooms.first() or room_a

            # Assign Chief Invigilator
            chief_lecturer = lecturers[i % len(lecturers)]
            InvigilatorDuty.objects.get_or_create(
                examination=exam,
                lecturer=chief_lecturer,
                defaults={
                    'room': assigned_room,
                    'role': 'CHIEF_INVIGILATOR',
                    'status': 'CONFIRMED'
                }
            )

            # Assign Assistant / Invigilator
            assistant_lecturer = lecturers[(i + 1) % len(lecturers)]
            InvigilatorDuty.objects.get_or_create(
                examination=exam,
                lecturer=assistant_lecturer,
                defaults={
                    'room': assigned_room,
                    'role': 'INVIGILATOR',
                    'status': 'ASSIGNED'
                }
            )

        # -------------------------------------------------------------
        # 9. SAMPLE EXAM ATTENDANCE & BOOKLET SERIAL NUMBERS
        # -------------------------------------------------------------
        # Generate attendance records for the first scheduled exam (CSC401)
        primary_exam = examinations[0]
        allocated_room = Room.objects.filter(allocated_exams__examination=primary_exam).first() or room_a
        recording_lecturer = lecturers[0]

        for idx, student in enumerate(students, start=1):
            booklet_number = f"BKT-2026-{primary_exam.unit.code}-{idx:04d}"
            # Mark first 4 students present, 5th student absent to show realistic scenario
            is_present = (idx != 5)
            remarks = "Candidate verified via Student ID card & signature." if is_present else "Absent without prior notice."

            ExamAttendance.objects.get_or_create(
                examination=primary_exam,
                student=student,
                defaults={
                    'room': allocated_room,
                    'recorded_by': recording_lecturer,
                    'booklet_serial_number': booklet_number,
                    'is_present': is_present,
                    'remarks': remarks
                }
            )

        # -------------------------------------------------------------
        # 10. MALPRACTICE CASE & EVIDENCE ATTACHMENTS
        # -------------------------------------------------------------
        suspect_student = students[3] # std004 (Mary Wanjiku)
        case_number = "MAL-2026-00001"

        malpractice_case, created = MalpracticeCase.objects.get_or_create(
            case_number=case_number,
            defaults={
                'examination': primary_exam,
                'student': suspect_student,
                'room': allocated_room,
                'reported_by': recording_lecturer,
                'incident_type': 'Unauthorized Material',
                'description': (
                    'Student was found in possession of unauthorized handwritten formula sheets '
                    'concealed inside a scientific calculator case during the CSC401 examination.'
                ),
                'severity': 'HIGH',
                'status': 'UNDER_INVESTIGATION'
            }
        )

        if created or not malpractice_case.evidence_files.exists():
            # Evidence Attachment 1: Confiscated Cheat Sheet
            evidence_1_content = ContentFile(
                b"IMAGE_DATA_SAMPLE: Confiscated handwritten formula sheets for CSC401 examination.",
                name="confiscated_formula_notes.txt"
            )
            MalpracticeEvidence.objects.create(
                case=malpractice_case,
                file=evidence_1_content,
                description="Photograph / transcript of confiscated formula notes concealed in calculator cover"
            )

            # Evidence Attachment 2: Invigilator Incident Report
            evidence_2_content = ContentFile(
                b"REPORT_DATA_SAMPLE: Formal written statement from Chief Invigilator detailing time and nature of incident.",
                name="invigilator_statement.txt"
            )
            MalpracticeEvidence.objects.create(
                case=malpractice_case,
                file=evidence_2_content,
                description="Signed Chief Invigilator statement and incident log entry"
            )

        # -------------------------------------------------------------
        # 11. UPLOADED MARKS (For 3-Way Reconciliation Testing)
        # -------------------------------------------------------------
        # Student 1: Clean (Present, Booklet Issued, Marks: 26+58 = 84 -> A)
        StudentMark.objects.get_or_create(
            student=students[0],
            examination=primary_exam,
            defaults={
                'coursework_mark': 26.00,
                'exam_mark': 58.00,
                'submitted_by': recording_lecturer,
                'status': 'SUBMITTED'
            }
        )

        # Student 2: Clean (Present, Booklet Issued, Marks: 22+50 = 72 -> A)
        StudentMark.objects.get_or_create(
            student=students[1],
            examination=primary_exam,
            defaults={
                'coursework_mark': 22.00,
                'exam_mark': 50.00,
                'submitted_by': recording_lecturer,
                'status': 'SUBMITTED'
            }
        )

        # Student 3: (Present with Booklet, NO MARKS -> Will trigger UNSUBMITTED_MARKS anomaly)

        # Student 4: (Present with Booklet & Malpractice -> Marks submitted: 18+42 = 60 -> B)
        StudentMark.objects.get_or_create(
            student=students[3],
            examination=primary_exam,
            defaults={
                'coursework_mark': 18.00,
                'exam_mark': 42.00,
                'submitted_by': recording_lecturer,
                'status': 'SUBMITTED'
            }
        )

        # Student 5: (Marked ABSENT in hall, but has uploaded marks -> Will trigger GHOST_MARKS anomaly)
        StudentMark.objects.get_or_create(
            student=students[4],
            examination=primary_exam,
            defaults={
                'coursework_mark': 20.00,
                'exam_mark': 45.00,
                'submitted_by': recording_lecturer,
                'status': 'SUBMITTED'
            }
        )

        self.stdout.write(self.style.SUCCESS("Successfully seeded database with full suite of test data!"))
        self.stdout.write(self.style.SUCCESS(f"- Exam Period: {period.name} (ID: {period.id})"))
        self.stdout.write(self.style.SUCCESS(f"- Test Lecturers: lec001 to lec003 (Password: Password123!)"))
        self.stdout.write(self.style.SUCCESS(f"- Test Students: std001 to std005 (Password: Password123!)"))
        self.stdout.write(self.style.SUCCESS(f"- Invigilator Duties: Assigned for all {len(examinations)} examinations."))
        self.stdout.write(self.style.SUCCESS(f"- Exam Attendance: {len(students)} booklet serial records created for {primary_exam.unit.code}."))
        self.stdout.write(self.style.SUCCESS(f"- Malpractice Case: Created Case #{case_number} with 2 evidence attachments."))
        self.stdout.write(self.style.SUCCESS(f"- Assessment Marks: Uploaded marks for reconciliation test scenarios."))