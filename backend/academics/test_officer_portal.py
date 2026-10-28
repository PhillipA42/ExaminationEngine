from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from authentication.models import Role, User, UserRole
from academics.models import (
    Course,
    Department,
    Lecturer,
    Notification,
    ReconciliationAnomaly,
    ReconciliationReport,
    School,
    Student,
    Unit,
)
from locations.models import Building, Campus, Floor, Room
from malpractice.models import MalpracticeCase
from invigilators.models import InvigilationAudit, InvigilatorDuty
from scheduling.models import (
    ExamSchedule,
    Examination,
    ExaminationPeriod,
    StudentExamAllocation,
)


class OfficerPortalTests(TestCase):
    def setUp(self):
        self.officer = User.objects.create_user(
            username="officer-test",
            email="officer-test@example.test",
            password="Valid-Temp-Pass-123!",
        )
        self.role, _ = Role.objects.get_or_create(name=Role.EXAM_OFFICER)
        UserRole.objects.create(user=self.officer, role=self.role)
        self.client.force_login(self.officer)

    def test_dashboard_uses_officer_control_centre(self):
        Notification.objects.create(
            recipient=self.officer,
            title="Schedule update",
            message="A schedule needs review.",
            notification_type="ADMIN_ALERT",
        )
        response = self.client.get(reverse("officer_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Examination Control Centre")
        self.assertContains(response, "Requires attention")
        self.assertContains(response, "Upcoming examinations")
        self.assertContains(response, "ExaminationEngine")
        self.assertContains(response, "Examination Officer Portal")
        self.assertContains(response, 'aria-label="Notifications, 1 unread"')
        self.assertContains(response, "officerMobileMenu")

    def test_operational_workspaces_render_for_officer(self):
        sections = [
            "periods",
            "examinations",
            "rooms",
            "allocations",
            "invigilators",
            "eligibility",
            "attendance",
            "booklets",
            "malpractice",
            "reconciliation",
            "anomalies",
            "verification",
            "notifications",
            "reports",
            "integrations",
            "profile",
            "sessions",
        ]
        for section in sections:
            with self.subTest(section=section):
                response = self.client.get(
                    reverse("officer_workspace", kwargs={"section": section})
                )
                self.assertEqual(response.status_code, 200)

    def test_timetable_workspace_opens_selected_period_planner(self):
        response = self.client.get(
            reverse("officer_workspace", kwargs={"section": "timetable"})
        )

        self.assertRedirects(
            response,
            reverse("officer_workspace", kwargs={"section": "periods"}),
            fetch_redirect_response=False,
        )

    def test_non_officer_cannot_open_officer_dashboard(self):
        self.client.logout()
        user = User.objects.create_user(
            username="student-like",
            email="student-like@example.test",
            password="Valid-Temp-Pass-123!",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("officer_dashboard"))

        self.assertEqual(response.status_code, 403)

    def _examination_setup(self):
        school = School.objects.create(code="OPS", name="Operations School")
        department = Department.objects.create(
            school=school,
            code="OPS",
            name="Operations Department",
        )
        course = Course.objects.create(
            department=department,
            code="BOPS",
            name="Operations",
        )
        unit = Unit.objects.create(course=course, code="OPS101", name="Operations")
        period = ExaminationPeriod.objects.create(
            name="Officer Portal Test Period",
            academic_year="2026/2027",
            semester=1,
            start_date="2026-10-01",
            end_date="2026-12-01",
            status="PUBLISHED",
            created_by=self.officer,
        )
        examination = Examination.objects.create(
            period=period,
            unit=unit,
            created_by=self.officer,
        )
        student_user = User.objects.create_user(
            username="case-student",
            email="case-student@example.test",
            password="Valid-Temp-Pass-123!",
        )
        student = Student.objects.create(
            user=student_user,
            registration_number="OP/001/26",
            course=course,
            department=department,
            school=school,
        )
        campus = Campus.objects.create(code="OP", name="Operations Campus")
        building = Building.objects.create(campus=campus, code="OP", name="Operations Block")
        floor = Floor.objects.create(building=building, floor_number=0, name="Ground Floor")
        room = Room.objects.create(
            floor=floor,
            building=building,
            room_number="OP-01",
            name="Operations Hall",
            capacity=100,
        )
        return period, examination, student, room

    def test_officer_can_create_period_and_examination(self):
        response = self.client.post(
            reverse("officer_create_period"),
            {
                "name": "New Officer Test Period",
                "academic_year": "2026/2027",
                "semester": 2,
                "start_date": "2027-01-10",
                "end_date": "2027-02-10",
            },
        )
        self.assertEqual(response.status_code, 302)
        period = ExaminationPeriod.objects.get(name="New Officer Test Period")
        self.assertEqual(period.created_by, self.officer)

        _, examination, _, _ = self._examination_setup()
        response = self.client.post(
            reverse("officer_create_examination"),
            {
                "period": examination.period_id,
                "unit": examination.unit_id,
                "examination_type": "CAT",
                "duration_minutes": 120,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Examination.objects.filter(
                period=examination.period,
                unit=examination.unit,
                examination_type="CAT",
            ).exists()
        )

    def test_officer_case_review_uses_existing_malpractice_workflow(self):
        _, examination, student, room = self._examination_setup()
        case = MalpracticeCase.objects.create(
            case_number="MAL-OFFICER-001",
            examination=examination,
            student=student,
            room=room,
            incident_type="Unauthorized material",
            description="Reported allegation for testing.",
        )

        response = self.client.get(reverse("officer_case_detail", args=[case.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Allegation, not a finding")

        response = self.client.post(
            reverse("officer_case_detail", args=[case.id]),
            {
                "action": "transition",
                "status": "UNDER_INVESTIGATION",
                "determination": "Evidence review started.",
            },
        )
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.status, "UNDER_INVESTIGATION")
        self.assertEqual(case.audit_events.count(), 1)

    def test_officer_can_resolve_anomaly_with_notes(self):
        _, examination, student, _ = self._examination_setup()
        report = ReconciliationReport.objects.create(examination=examination)
        anomaly = ReconciliationAnomaly.objects.create(
            report=report,
            student=student,
            anomaly_type="OTHER_IRREGULARITY",
            description="Test discrepancy.",
        )
        response = self.client.post(
            reverse("officer_anomaly_detail", args=[anomaly.id]),
            {"resolution_notes": "Compared against the authorized source record."},
        )
        self.assertEqual(response.status_code, 302)
        anomaly.refresh_from_db()
        self.assertTrue(anomaly.is_resolved)
        self.assertEqual(anomaly.resolved_by, self.officer)
        self.assertIsNotNone(anomaly.resolved_at)

    def test_room_allocation_uses_existing_capacity_validation(self):
        _, examination, _, room = self._examination_setup()
        response = self.client.post(
            reverse("officer_workspace", kwargs={"section": "rooms"}),
            {
                "action": "allocate_room",
                "examination": examination.id,
                "room": room.id,
                "allocated_capacity": room.capacity + 1,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(examination.room_allocations.exists())

        response = self.client.post(
            reverse("officer_workspace", kwargs={"section": "rooms"}),
            {
                "action": "allocate_room",
                "examination": examination.id,
                "room": room.id,
                "allocated_capacity": room.capacity,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(examination.room_allocations.filter(room=room).exists())

    def test_officer_can_create_room_and_open_allocation_planner(self):
        _, examination, student, existing_room = self._examination_setup()
        response = self.client.post(
            reverse("officer_create_room"),
            {
                "building": existing_room.building_id,
                "floor": existing_room.floor_id,
                "room_number": "OP-02",
                "name": "Operations Lab",
                "capacity": 40,
                "room_type": "LAB",
                "description": "",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Room.objects.filter(room_number="OP-02", name="Operations Lab").exists())

        StudentExamAllocation.objects.create(
            examination=examination,
            student=student,
            room=existing_room,
        )
        response = self.client.get(
            reverse("officer_workspace", kwargs={"section": "allocations"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("period_timetable_planner", args=[examination.period_id]),
        )

    def test_invigilator_assignment_uses_existing_service_and_audit(self):
        _, examination, student, room = self._examination_setup()
        ExamSchedule.objects.create(
            examination=examination,
            exam_date=date(2026, 11, 10),
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        StudentExamAllocation.objects.create(
            examination=examination,
            student=student,
            room=room,
        )
        lecturer_user = User.objects.create_user(
            username="duty-lecturer",
            email="duty-lecturer@example.test",
            password="Valid-Temp-Pass-123!",
        )
        lecturer = Lecturer.objects.create(
            user=lecturer_user,
            staff_number="DUTY-001",
            department=examination.unit.course.department,
        )

        response = self.client.post(
            reverse("officer_workspace", kwargs={"section": "invigilators"}),
            {
                "action": "assign_invigilator",
                "examination": examination.id,
                "lecturer": lecturer.id,
                "room": room.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        duty = InvigilatorDuty.objects.get(examination=examination, lecturer=lecturer)
        audit = InvigilationAudit.objects.get(duty=duty)
        self.assertEqual(audit.action, "DUTY_ASSIGNED")
        self.assertEqual(audit.details["actor_user_id"], self.officer.id)
