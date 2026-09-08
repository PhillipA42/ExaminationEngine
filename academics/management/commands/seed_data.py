from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from authentication.models import Role, UserRole
from academics.models import School, Department, Course, Unit, Student, UnitRegistration, Lecturer
from locations.models import Campus, Building, Floor, Room
from scheduling.models import ExaminationPeriod, Examination

User = get_user_model()

class Command(BaseCommand):
    help = 'Seeds database with realistic test data for timetable testing.'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Seeding database with test data...'))

        # -------------------------------------------------------------
        # 1. ROLES
        # -------------------------------------------------------------
        student_role, _ = Role.objects.get_or_create(name=Role.STUDENT, defaults={'description': 'Student Role'})
        lecturer_role, _ = Role.objects.get_or_create(name=Role.LECTURER, defaults={'description': 'Lecturer Role'})

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
        # 4. STUDENTS & UNIT REGISTRATION
        # -------------------------------------------------------------
        students_data = [
            ('std001', 'std001@university.ac.ke', 'CT101/0001/26', 'John', 'Doe'),
            ('std002', 'std002@university.ac.ke', 'CT101/0002/26', 'Jane', 'Smith'),
            ('std003', 'std003@university.ac.ke', 'CT101/0003/26', 'Alex', 'Kipchoge'),
            ('std004', 'std004@university.ac.ke', 'CT101/0004/26', 'Mary', 'Wanjiku'),
            ('std005', 'std005@university.ac.ke', 'CT101/0005/26', 'Brian', 'Ochieng'),
        ]

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
        # 5. EXAMINATION PERIOD & EXAMINATIONS
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

        for unit in units:
            Examination.objects.get_or_create(
                period=period,
                unit=unit,
                examination_type='FINAL',
                defaults={
                    'duration_minutes': 180,
                    'status': 'PENDING'
                }
            )

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded database!"))
        self.stdout.write(self.style.SUCCESS(f"Created Exam Period ID: {period.id} ('{period.name}')"))
        self.stdout.write(self.style.SUCCESS(f"Test Student Usernames: std001 through std005 (Password: Password123!)"))