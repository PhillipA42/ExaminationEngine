import csv
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.utils.text import get_valid_filename

from academics.models import Lecturer
from academics.officer_forms import ExaminationForm, ExaminationPeriodForm, RoomForm
from academics.models import (
    EligibilityEvaluation,
    Notification,
    ReconciliationAnomaly,
    ReconciliationReport,
    ResultWorkflowAudit,
    UnitRegistration,
)
from invigilators.models import (
    ExamAttendance,
    ExaminationSession,
    InvigilationAudit,
    InvigilatorDuty,
)
from invigilators.serializers import InvigilatorDutySerializer
from locations.models import Building, Campus, Room
from malpractice.models import MalpracticeAudit, MalpracticeCase, MalpracticeEvidence
from malpractice.services import MalpracticeError, MalpracticeService
from academics.reconciliation import ExamReconciliationEngine
from scheduling.models import (
    Examination,
    ExaminationPeriod,
    ExamRoomAllocation,
    StudentExamAllocation,
)
from scheduling.serializers import ExamRoomAllocationSerializer
from verification.models import SystemVerificationReport
from verification.services import VerificationRunner


def officer_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (
            request.user.is_superuser
            or getattr(request.user, "is_exam_officer", False)
        ):
            return HttpResponseForbidden(
                "Access restricted: Examination Officer authorization required."
            )
        return view_func(request, *args, **kwargs)

    return wrapped


def _selected_period():
    today = timezone.localdate()
    periods = ExaminationPeriod.objects.order_by("start_date")
    current = periods.filter(
        start_date__lte=today,
        end_date__gte=today,
        status__in=["GENERATING", "PUBLISHED"],
    ).first()
    if current:
        return current
    upcoming = periods.filter(
        start_date__gte=today,
        status__in=["DRAFT", "GENERATING", "PUBLISHED"],
    ).first()
    if upcoming:
        return upcoming
    return periods.order_by("-start_date").first()


def _operational_activity():
    events = []
    for event in InvigilationAudit.objects.select_related("actor__user").order_by(
        "-created_at"
    )[:8]:
        actor = event.actor.user.get_full_name() if event.actor else "Unknown actor"
        events.append(
            {
                "at": event.created_at,
                "label": event.action.replace("_", " ").title(),
                "description": f"{actor} · {event.details or event.action}",
            }
        )
    for event in ResultWorkflowAudit.objects.select_related(
        "actor", "submission__unit"
    ).order_by("-timestamp")[:8]:
        actor = event.actor.get_full_name() if event.actor else "Unknown actor"
        events.append(
            {
                "at": event.timestamp,
                "label": event.action.replace("_", " ").title(),
                "description": (
                    f"{event.submission.unit.code} · {actor} · "
                    f"{event.previous_status or '—'} → {event.new_status or '—'}"
                ),
            }
        )
    for event in MalpracticeAudit.objects.select_related("actor__user", "case").order_by(
        "-created_at"
    )[:8]:
        actor = event.actor.user.get_full_name() if event.actor else "Unknown actor"
        events.append(
            {
                "at": event.created_at,
                "label": f"Case {event.action.replace('_', ' ').title()}",
                "description": f"{event.case.case_number} · {actor}",
            }
        )
    for report in SystemVerificationReport.objects.exclude(status="RUNNING").order_by(
        "-started_at"
    )[:5]:
        events.append(
            {
                "at": report.completed_at or report.started_at,
                "label": f"Verification {report.get_status_display()}",
                "description": (
                    f"{report.reference} · {report.passed_checks} passed, "
                    f"{report.failed_checks} failed"
                ),
            }
        )
    return sorted(events, key=lambda event: event["at"], reverse=True)[:8]


@officer_required
def officer_control_dashboard(request):
    period = _selected_period()
    period_examinations = (
        Examination.objects.filter(period=period)
        .select_related("unit", "period")
        .prefetch_related("room_allocations", "invigilator_duties")
        if period
        else Examination.objects.none()
    )
    examination_count = period_examinations.count()
    scheduled_count = period_examinations.filter(schedule__isnull=False).count()
    unscheduled_count = examination_count - scheduled_count
    schedule_percent = round(100 * scheduled_count / examination_count) if examination_count else 0
    unassigned_count = period_examinations.filter(
        invigilator_duties__isnull=True
    ).distinct().count()

    room_issue_count = 0
    if period:
        for examination in period_examinations:
            allocated_capacity = sum(
                allocation.allocated_capacity
                for allocation in examination.room_allocations.all()
            )
            registered_count = UnitRegistration.objects.filter(
                unit=examination.unit,
                academic_year=period.academic_year,
                semester=period.semester,
                registration_status="REGISTERED",
            ).count()
            if not examination.room_allocations.exists() or allocated_capacity < registered_count:
                room_issue_count += 1

    sessions = ExaminationSession.objects.select_related(
        "examination__unit", "room__building__campus"
    ).filter(status="OPEN")
    active_session_count = sessions.count()
    unresolved_anomaly_count = ReconciliationAnomaly.objects.filter(
        is_resolved=False,
        report__examination__period=period,
    ).count() if period else 0
    eligibility_exceptions = EligibilityEvaluation.objects.filter(
        examination_period=period,
        is_eligible=False,
    ).count() if period else 0
    unresolved_malpractice_count = MalpracticeCase.objects.exclude(
        status__in=["DISMISS", "SANCTIONED"]
    ).count()
    missing_booklet_count = ExamAttendance.objects.filter(
        examination__period=period,
        is_present=True,
        booklet_serial_number__isnull=True,
    ).count() if period else 0
    latest_verification = SystemVerificationReport.objects.order_by("-started_at").first()
    verification_attention = bool(
        latest_verification and latest_verification.status in ["FAILED", "PASSED_WITH_WARNINGS"]
    )

    upcoming = (
        Examination.objects.filter(
            period=period,
            schedule__exam_date__gte=timezone.localdate(),
        )
        .select_related("unit", "schedule")
        .prefetch_related(
            "room_allocations__room__building__campus",
            "invigilator_duties",
            "student_allocations",
        )
        .order_by("schedule__exam_date", "schedule__start_time")[:8]
        if period
        else []
    )
    upcoming_items = []
    for examination in upcoming:
        rooms = [
            f"{allocation.room.building.campus.name} · "
            f"{allocation.room.building.name} · {allocation.room.name}"
            for allocation in examination.room_allocations.all()
        ]
        schedule = examination.schedule
        upcoming_items.append(
            {
                "examination": examination,
                "schedule": schedule,
                "venue": ", ".join(rooms) if rooms else "Room not allocated",
                "allocated_students": examination.student_allocations.count(),
                "has_invigilators": examination.invigilator_duties.exists(),
            }
        )

    attention_items = []
    if unscheduled_count:
        attention_items.append(
            {
                "severity": "warning",
                "title": "Examinations awaiting scheduling",
                "count": unscheduled_count,
                "url": reverse("officer_workspace", kwargs={"section": "examinations"}),
            }
        )
    if room_issue_count:
        attention_items.append(
            {
                "severity": "warning",
                "title": "Examinations without enough room capacity",
                "count": room_issue_count,
                "url": reverse("officer_workspace", kwargs={"section": "rooms"}),
            }
        )
    if unassigned_count:
        attention_items.append(
            {
                "severity": "warning",
                "title": "Examinations without invigilator assignments",
                "count": unassigned_count,
                "url": reverse("officer_workspace", kwargs={"section": "invigilators"}),
            }
        )
    if unresolved_anomaly_count:
        attention_items.append(
            {
                "severity": "danger",
                "title": "Unresolved reconciliation anomalies",
                "count": unresolved_anomaly_count,
                "url": reverse("officer_workspace", kwargs={"section": "anomalies"}),
            }
        )
    if eligibility_exceptions:
        attention_items.append(
            {
                "severity": "warning",
                "title": "Eligibility exceptions",
                "count": eligibility_exceptions,
                "url": reverse("officer_workspace", kwargs={"section": "eligibility"}),
            }
        )
    if missing_booklet_count:
        attention_items.append(
            {
                "severity": "danger",
                "title": "Present students missing booklet serial records",
                "count": missing_booklet_count,
                "url": reverse("officer_workspace", kwargs={"section": "booklets"}),
            }
        )
    if unresolved_malpractice_count:
        attention_items.append(
            {
                "severity": "danger",
                "title": "Malpractice cases awaiting resolution",
                "count": unresolved_malpractice_count,
                "url": reverse("officer_workspace", kwargs={"section": "malpractice"}),
            }
        )
    if verification_attention:
        attention_items.append(
            {
                "severity": "danger",
                "title": "Latest system verification needs review",
                "count": latest_verification.failed_checks + latest_verification.warnings,
                "url": reverse("officer_workspace", kwargs={"section": "verification"}),
            }
        )
    if period:
        from scheduling.services import ExamConstraintChecker

        conflict_report = ExamConstraintChecker.validate_period_entire_timetable(period)
        if conflict_report.hard_violations:
            attention_items.append(
                {
                    "severity": "danger",
                    "title": "Timetable hard conflicts",
                    "count": len(conflict_report.hard_violations),
                    "url": reverse("period_timetable_planner", args=[period.id]),
                }
            )

    return render(
        request,
        "officer/dashboard.html",
        {
            "period": period,
            "examination_count": examination_count,
            "scheduled_count": scheduled_count,
            "unscheduled_count": unscheduled_count,
            "schedule_percent": schedule_percent,
            "active_session_count": active_session_count,
            "unassigned_count": unassigned_count,
            "room_issue_count": room_issue_count,
            "unresolved_anomaly_count": unresolved_anomaly_count,
            "eligibility_exceptions": eligibility_exceptions,
            "latest_verification": latest_verification,
            "attention_items": attention_items,
            "upcoming_items": upcoming_items,
            "active_sessions": sessions[:6],
            "recent_activity": _operational_activity(),
        },
    )


@officer_required
def officer_workspace(request, section):
    """Read-only operational workspaces backed by existing examination records."""
    if section == "rooms" and request.method == "POST" and request.POST.get("action") == "allocate_room":
        examination = get_object_or_404(Examination, pk=request.POST.get("examination"))
        room = get_object_or_404(Room, pk=request.POST.get("room"))
        allocation = ExamRoomAllocation.objects.filter(
            examination=examination,
            room=room,
        ).first()
        serializer = ExamRoomAllocationSerializer(
            instance=allocation,
            data=request.POST,
            context={"request": request},
        )
        if serializer.is_valid():
            serializer.save()
            messages.success(request, f"{room.name} room allocation saved for {examination.unit.code}.")
        else:
            messages.error(request, f"Room allocation was not saved: {serializer.errors}")
        return redirect("officer_workspace", section="rooms")
    if section == "invigilators" and request.method == "POST" and request.POST.get("action") == "assign_invigilator":
        examination = get_object_or_404(Examination, pk=request.POST.get("examination"))
        lecturer = get_object_or_404(Lecturer, pk=request.POST.get("lecturer"))
        duty = InvigilatorDuty.objects.filter(
            examination=examination,
            lecturer=lecturer,
        ).first()
        serializer = InvigilatorDutySerializer(
            instance=duty,
            data=request.POST,
            context={"request": request},
        )
        if serializer.is_valid():
            previous = (
                {
                    "room_id": duty.room_id,
                    "role": duty.role,
                    "status": duty.status,
                }
                if duty
                else None
            )
            with transaction.atomic():
                duty = serializer.save()
                InvigilationAudit.objects.create(
                    duty=duty,
                    actor=getattr(request.user, "lecturer_profile", None),
                    action="DUTY_UPDATED" if previous else "DUTY_ASSIGNED",
                    details={
                        "actor_user_id": request.user.pk,
                        "previous": previous,
                        "room_id": duty.room_id,
                        "role": duty.role,
                        "status": duty.status,
                    },
                )
            messages.success(
                request,
                f"{lecturer.user.get_full_name()} was assigned to {examination.unit.code}.",
            )
        else:
            messages.error(request, f"Invigilator assignment was not saved: {serializer.errors}")
        return redirect("officer_workspace", section="invigilators")
    if section == "verification" and request.method == "POST":
        if request.POST.get("action") == "run_verification":
            report = VerificationRunner(user=request.user).run()
            messages.success(
                request,
                f"Verification {report.reference} completed with {report.failed_checks} failed checks and {report.warnings} warnings.",
            )
            return redirect("officer_workspace", section="verification")
    if section == "notifications" and request.method == "POST":
        if request.POST.get("action") == "mark_all_read":
            updated = Notification.objects.filter(
                recipient=request.user,
                is_read=False,
            ).update(is_read=True, read_at=timezone.now())
            messages.success(request, f"Marked {updated} notification(s) as read.")
            return redirect("officer_workspace", section="notifications")
    if section == "eligibility" and request.method == "POST":
        period = get_object_or_404(ExaminationPeriod, pk=request.POST.get("period_id"))
        students = UnitRegistration.objects.filter(
            unit__examinations__period=period,
            academic_year=period.academic_year,
            semester=period.semester,
            registration_status="REGISTERED",
        ).values_list("student_id", flat=True).distinct()
        from academics.eligibility_services import EligibilityService
        from academics.models import Student

        service = EligibilityService()
        evaluated = 0
        for student in Student.objects.filter(pk__in=students).iterator():
            service.evaluate(student, period, force_refresh=True)
            evaluated += 1
        messages.success(
            request,
            f"Eligibility refreshed for {evaluated} allocated students in {period.name}.",
        )
        return redirect("officer_workspace", section="eligibility")

    search = request.GET.get("q", "").strip()
    title = ""
    description = ""
    columns = []
    rows = []
    count = 0
    page_obj = None
    available_examinations = []
    available_rooms = []
    available_lecturers = []
    available_periods = []
    available_campuses = []
    available_buildings = []
    status_filter_options = []
    planner_period = None
    status_filter = ""
    date_filter = ""
    allocation_filter = ""
    campus_filter = ""
    building_filter = ""
    capacity_filter = ""

    if section == "periods":
        title = "Examination Periods"
        description = "Review examination periods and open the existing timetable planner."
        periods = ExaminationPeriod.objects.annotate(
            examination_total=Count("examinations", distinct=True)
        ).order_by("-start_date")
        if search:
            periods = periods.filter(Q(name__icontains=search) | Q(academic_year__icontains=search))
        page_obj = Paginator(periods, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Period", "Academic year", "Semester", "Dates", "Status", "Examinations"]
        rows = [
            {
                "cells": [
                    period.name,
                    period.academic_year,
                    period.semester,
                    f"{period.start_date:%d %b %Y} – {period.end_date:%d %b %Y}",
                    period.get_status_display() if hasattr(period, "get_status_display") else period.status,
                    period.examination_total,
                ],
                "action_url": reverse("period_timetable_planner", args=[period.id]),
                "action_label": "Open planner",
                "edit_url": reverse("officer_edit_period", args=[period.id]),
            }
            for period in page_obj
        ]
    elif section in {"examinations", "timetable"}:
        if section == "timetable":
            period = _selected_period()
            if period:
                return redirect("period_timetable_planner", period_id=period.id)
            return redirect("officer_workspace", section="periods")
        title = "Examinations"
        description = "Search examinations, view their scheduling and allocation state, or open the scheduler."
        examinations = Examination.objects.select_related(
            "period", "unit", "unit__course__department"
        ).prefetch_related("room_allocations", "invigilator_duties").order_by(
            "period__start_date", "unit__code"
        )
        available_periods = ExaminationPeriod.objects.order_by("-start_date")
        status_filter_options = list(
            Examination.objects.order_by()
            .values_list("status", flat=True)
            .distinct()
        )
        if search:
            examinations = examinations.filter(
                Q(unit__code__icontains=search)
                | Q(unit__name__icontains=search)
                | Q(period__name__icontains=search)
            )
        period_id = request.GET.get("period", "").strip()
        if period_id.isdigit():
            examinations = examinations.filter(period_id=int(period_id))
        status_filter = request.GET.get("status", "").strip()
        if status_filter:
            examinations = examinations.filter(status=status_filter)
        date_filter = request.GET.get("date", "").strip()
        if date_filter:
            examinations = examinations.filter(schedule__exam_date=date_filter)
        allocation_filter = request.GET.get("allocation", "").strip()
        if allocation_filter == "allocated":
            examinations = examinations.filter(student_allocations__isnull=False).distinct()
        elif allocation_filter == "unallocated":
            examinations = examinations.filter(student_allocations__isnull=True)
        page_obj = Paginator(examinations, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Unit", "Period", "Date and time", "Students", "Rooms", "Invigilators", "Status"]
        rows = []
        for exam in page_obj:
            schedule = getattr(exam, "schedule", None)
            schedule_display = (
                f"{schedule.exam_date:%d %b %Y} · {schedule.start_time:%H:%M}–{schedule.end_time:%H:%M}"
                if schedule else "Unscheduled"
            )
            rows.append(
                {
                    "cells": [
                        f"{exam.unit.code} · {exam.unit.name}",
                        exam.period.name,
                        schedule_display,
                        exam.registered_students_count,
                        exam.room_allocations.count(),
                        exam.invigilator_duties.count(),
                        exam.get_status_display() if hasattr(exam, "get_status_display") else exam.status,
                    ],
                    "action_url": reverse("manual_schedule_exam", args=[exam.id]),
                    "action_label": "Schedule",
                    "edit_url": reverse("officer_edit_examination", args=[exam.id]),
                }
            )
    elif section == "rooms":
        title = "Rooms & Venues"
        description = "View the campus-to-room hierarchy and current room capacities."
        rooms = Room.objects.select_related("building__campus", "floor").order_by(
            "building__campus__name", "building__name", "room_number"
        )
        if search:
            rooms = rooms.filter(
                Q(name__icontains=search)
                | Q(room_number__icontains=search)
                | Q(building__name__icontains=search)
                | Q(building__campus__name__icontains=search)
            )
        available_examinations = Examination.objects.select_related(
            "unit", "period"
        ).order_by("-period__start_date", "unit__code")
        available_rooms = Room.objects.filter(status="ACTIVE").order_by(
            "building__name", "room_number"
        )
        available_campuses = Campus.objects.order_by("name")
        available_buildings = Building.objects.select_related("campus").order_by(
            "campus__name", "name"
        )
        campus_filter = request.GET.get("campus", "").strip()
        building_filter = request.GET.get("building", "").strip()
        capacity_filter = request.GET.get("capacity", "").strip()
        if campus_filter.isdigit():
            rooms = rooms.filter(building__campus_id=int(campus_filter))
        if building_filter.isdigit():
            rooms = rooms.filter(building_id=int(building_filter))
        if capacity_filter.isdigit():
            rooms = rooms.filter(capacity__gte=int(capacity_filter))
        page_obj = Paginator(rooms, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Campus", "Building", "Floor", "Room", "Capacity", "Type", "Status"]
        rows = [
            {
                "cells": [
                    room.building.campus.name,
                    room.building.name,
                    room.floor.name,
                    f"{room.name} ({room.room_number})",
                    room.capacity,
                    room.room_type.replace("_", " ").title(),
                    room.status.title(),
                ],
                "edit_url": reverse("officer_edit_room", args=[room.id]),
            }
            for room in page_obj
        ]
    elif section == "allocations":
        title = "Student Examination Allocation"
        description = "Review allocations. The existing timetable engine creates the authoritative student-room allocations from registrations and room capacity."
        allocations = StudentExamAllocation.objects.select_related(
            "examination__unit", "examination__period", "student__user", "room__building"
        ).order_by("examination__schedule__exam_date", "student__registration_number")
        available_periods = ExaminationPeriod.objects.order_by("-start_date")
        period_filter = request.GET.get("period", "").strip()
        if period_filter.isdigit():
            allocations = allocations.filter(examination__period_id=int(period_filter))
            planner_period = ExaminationPeriod.objects.filter(pk=int(period_filter)).first()
        if planner_period is None:
            planner_period = _selected_period()
        if search:
            allocations = allocations.filter(
                Q(student__registration_number__icontains=search)
                | Q(student__user__first_name__icontains=search)
                | Q(student__user__last_name__icontains=search)
                | Q(examination__unit__code__icontains=search)
            )
        page_obj = Paginator(allocations, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Student number", "Student", "Examination", "Period", "Room", "Seat", "Status"]
        rows = [
            {
                "cells": [
                    allocation.student.registration_number,
                    allocation.student.user.get_full_name(),
                    allocation.examination.unit.code,
                    allocation.examination.period.name,
                    f"{allocation.room.building.name} · {allocation.room.name}",
                    allocation.seat_number or "Not assigned",
                    allocation.status.title(),
                ],
                "action_url": reverse(
                    "period_timetable_planner",
                    args=[allocation.examination.period_id],
                ),
                "action_label": "Open planner",
            }
            for allocation in page_obj
        ]
    elif section == "invigilators":
        title = "Invigilator Assignments"
        description = "Review lecturers assigned to examination room duties."
        duties = InvigilatorDuty.objects.select_related(
            "examination__unit", "examination__period", "lecturer__user",
            "room__building__campus",
        ).order_by("examination__schedule__exam_date", "examination__unit__code")
        if search:
            duties = duties.filter(
                Q(examination__unit__code__icontains=search)
                | Q(lecturer__user__first_name__icontains=search)
                | Q(lecturer__user__last_name__icontains=search)
                | Q(room__name__icontains=search)
            )
        available_examinations = Examination.objects.select_related(
            "unit", "period"
        ).order_by("-period__start_date", "unit__code")
        available_rooms = Room.objects.filter(status="ACTIVE").order_by(
            "building__name", "room_number"
        )
        available_lecturers = Lecturer.objects.filter(
            status="ACTIVE",
            user__is_active=True,
        ).select_related("user").order_by("user__last_name", "user__first_name")
        page_obj = Paginator(duties, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Examination", "Period", "Invigilator", "Venue", "Duty role", "Status"]
        rows = [
            {
                "cells": [
                    duty.examination.unit.code,
                    duty.examination.period.name,
                    duty.lecturer.user.get_full_name(),
                    f"{duty.room.building.name} · {duty.room.name}",
                    duty.role.replace("_", " ").title(),
                    duty.status.title(),
                ],
                "action_url": reverse("officer_sessions"),
                "action_label": "View sessions",
            }
            for duty in page_obj
        ]
    elif section == "eligibility":
        title = "Examination Eligibility"
        description = "Review the latest stored eligibility evaluations. Eligibility is not manually overridden here."
        evaluations = EligibilityEvaluation.objects.select_related(
            "student__user", "examination_period"
        ).order_by("-evaluated_at")
        available_periods = ExaminationPeriod.objects.order_by("-start_date")
        if search:
            evaluations = evaluations.filter(
                Q(student__registration_number__icontains=search)
                | Q(student__user__first_name__icontains=search)
                | Q(student__user__last_name__icontains=search)
                | Q(examination_period__name__icontains=search)
            )
        page_obj = Paginator(evaluations, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Student number", "Student", "Period", "Result", "Rules passed", "Rules failed", "Evaluated"]
        rows = [
            {
                "cells": [
                    evaluation.student.registration_number,
                    evaluation.student.user.get_full_name(),
                    evaluation.examination_period.name,
                    evaluation.get_overall_status_display(),
                    evaluation.rules_passed,
                    evaluation.rules_failed,
                    timezone.localtime(evaluation.evaluated_at).strftime("%d %b %Y %H:%M"),
                ]
            }
            for evaluation in page_obj
        ]
    elif section in {"attendance", "booklets"}:
        booklet_mode = section == "booklets"
        title = "Booklet Tracking" if booklet_mode else "Attendance Monitoring"
        description = (
            "Inspect attendance records and missing booklet serials recorded by invigilators."
            if booklet_mode
            else "Monitor attendance records recorded through authorized invigilator sessions."
        )
        attendance = ExamAttendance.objects.select_related(
            "examination__unit", "examination__period", "student__user", "room"
        ).order_by("-check_in_time")
        if search:
            attendance = attendance.filter(
                Q(student__registration_number__icontains=search)
                | Q(examination__unit__code__icontains=search)
                | Q(booklet_serial_number__icontains=search)
                | Q(room__name__icontains=search)
            )
        if booklet_mode and request.GET.get("issue") == "missing":
            attendance = attendance.filter(is_present=True, booklet_serial_number__isnull=True)
        page_obj = Paginator(attendance, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Examination", "Student number", "Student", "Room", "Attendance", "Booklet serial", "Recorded"]
        rows = [
            {
                "cells": [
                    attendance_record.examination.unit.code,
                    attendance_record.student.registration_number,
                    attendance_record.student.user.get_full_name(),
                    attendance_record.room.name,
                    "Present" if attendance_record.is_present else "Absent",
                    attendance_record.booklet_serial_number or "Missing",
                    timezone.localtime(attendance_record.check_in_time).strftime("%d %b %Y %H:%M"),
                ]
            }
            for attendance_record in page_obj
        ]
    elif section == "sessions":
        title = "Examination Sessions"
        description = "Session state is taken from invigilator-created session records, not inferred from scheduled times."
        sessions = ExaminationSession.objects.select_related(
            "examination__unit", "examination__schedule", "room__building__campus"
        ).order_by("-opened_at")
        if search:
            sessions = sessions.filter(
                Q(examination__unit__code__icontains=search)
                | Q(room__name__icontains=search)
                | Q(room__building__name__icontains=search)
            )
        page_obj = Paginator(sessions, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Examination", "Scheduled time", "Venue", "Status", "Attendance", "Opened"]
        rows = [
            {
                "cells": [
                    session.examination.unit.code,
                    (
                        f"{session.examination.schedule.exam_date:%d %b %Y} · "
                        f"{session.examination.schedule.start_time:%H:%M}"
                        if hasattr(session.examination, "schedule") else "Not scheduled"
                    ),
                    f"{session.room.building.campus.name} · {session.room.building.name} · {session.room.name}",
                    session.get_status_display(),
                    f"{session.examination.attendance_records.filter(room=session.room).count()} / "
                    f"{session.examination.student_allocations.filter(room=session.room).count()}",
                    timezone.localtime(session.opened_at).strftime("%d %b %Y %H:%M"),
                ],
                "action_url": reverse("officer_session_detail", args=[session.id]),
                "action_label": "Monitor",
            }
            for session in page_obj
        ]
    elif section == "malpractice":
        title = "Malpractice Cases"
        description = "Reports are allegations for review; a reported case is not a finding of guilt."
        cases = MalpracticeCase.objects.select_related(
            "examination__unit", "student", "room", "reported_by__user"
        ).order_by("-reported_at")
        if search:
            cases = cases.filter(
                Q(case_number__icontains=search)
                | Q(student__registration_number__icontains=search)
                | Q(examination__unit__code__icontains=search)
                | Q(incident_type__icontains=search)
            )
        page_obj = Paginator(cases, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Case", "Student number", "Examination", "Allegation", "Severity", "Status", "Reported"]
        rows = [
            {
                "cells": [
                    case.case_number,
                    case.student.registration_number,
                    case.examination.unit.code,
                    case.incident_type,
                    case.get_severity_display(),
                    case.get_status_display(),
                    timezone.localtime(case.reported_at).strftime("%d %b %Y %H:%M"),
                ],
                "action_url": reverse("officer_case_detail", args=[case.id]),
                "action_label": "Review case",
            }
            for case in page_obj
        ]
    elif section == "reconciliation":
        title = "Reconciliation"
        description = "Review reports generated by the existing examination reconciliation service."
        reports = ReconciliationReport.objects.select_related(
            "examination__unit", "examination__period"
        ).order_by("-generated_at")
        available_examinations = Examination.objects.select_related(
            "unit", "period"
        ).order_by("-period__start_date", "unit__code")
        if search:
            reports = reports.filter(
                Q(examination__unit__code__icontains=search)
                | Q(examination__period__name__icontains=search)
                | Q(status__icontains=search)
            )
        page_obj = Paginator(reports, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Examination", "Period", "Registered", "Attended", "Booklets", "Anomalies", "Result", "Run"]
        rows = [
            {
                "cells": [
                    report.examination.unit.code,
                    report.examination.period.name,
                    report.total_registered,
                    report.total_attended,
                    report.total_booklets_issued,
                    report.total_anomalies,
                    report.get_status_display(),
                    timezone.localtime(report.generated_at).strftime("%d %b %Y %H:%M"),
                ],
                "action_url": f"{reverse('officer_workspace', kwargs={'section': 'anomalies'})}?report={report.id}",
                "action_label": "View anomalies",
            }
            for report in page_obj
        ]
    elif section == "anomalies":
        title = "Reconciliation Anomalies"
        description = "Investigate recorded discrepancies; opening an item does not mark it resolved."
        anomalies = ReconciliationAnomaly.objects.select_related(
            "report__examination__unit", "student"
        ).order_by("is_resolved", "-created_at")
        report_id = request.GET.get("report", "").strip()
        if report_id.isdigit():
            anomalies = anomalies.filter(report_id=int(report_id))
        if search:
            anomalies = anomalies.filter(
                Q(description__icontains=search)
                | Q(anomaly_type__icontains=search)
                | Q(report__examination__unit__code__icontains=search)
                | Q(student__registration_number__icontains=search)
            )
        page_obj = Paginator(anomalies, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Examination", "Student", "Anomaly", "Severity", "Status", "Detected"]
        rows = [
            {
                "cells": [
                    anomaly.report.examination.unit.code,
                    anomaly.student.registration_number if anomaly.student else "General",
                    anomaly.get_anomaly_type_display(),
                    anomaly.get_severity_display(),
                    "Resolved" if anomaly.is_resolved else "Unresolved",
                    timezone.localtime(anomaly.created_at).strftime("%d %b %Y %H:%M"),
                ],
                "action_url": reverse("officer_anomaly_detail", args=[anomaly.id]),
                "action_label": "View details",
            }
            for anomaly in page_obj
        ]
    elif section == "verification":
        title = "System Verification"
        description = "Review actual verification runs and check results. No success is inferred from loading this page."
        reports = SystemVerificationReport.objects.prefetch_related("checks").order_by("-started_at")
        if search:
            reports = reports.filter(
                Q(reference__icontains=search) | Q(status__icontains=search)
            )
        page_obj = Paginator(reports, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Reference", "Status", "Checks", "Passed", "Failed", "Warnings", "Started"]
        rows = [
            {
                "cells": [
                    report.reference,
                    report.get_status_display(),
                    report.total_checks,
                    report.passed_checks,
                    report.failed_checks,
                    report.warnings,
                    timezone.localtime(report.started_at).strftime("%d %b %Y %H:%M"),
                ]
            }
            for report in page_obj
        ]
    elif section == "notifications":
        title = "Notifications"
        description = "Your in-app notifications and their read status."
        notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")
        if search:
            notifications = notifications.filter(
                Q(title__icontains=search) | Q(message__icontains=search)
            )
        page_obj = Paginator(notifications, 25).get_page(request.GET.get("page"))
        count = page_obj.paginator.count
        columns = ["Notification", "Type", "Status", "Created"]
        rows = [
            {
                "cells": [
                    f"{notification.title} · {notification.message}",
                    notification.get_notification_type_display(),
                    "Read" if notification.is_read else "Unread",
                    timezone.localtime(notification.created_at).strftime("%d %b %Y %H:%M"),
                ],
                "action_url": notification.related_link if notification.related_link else "",
                "action_label": "Open",
            }
            for notification in page_obj
        ]
    elif section == "reports":
        title = "Operational Reports"
        description = "Export authorized examination and student allocation summaries."
        columns = ["Report", "Contents", "Action"]
        rows = [
            {
                "cells": ["Examination schedule", "Examinations with period, date, time and status"],
                "action_url": f"{reverse('officer_report_export')}?type=examinations",
                "action_label": "Download CSV",
            },
            {
                "cells": ["Student allocations", "Allocated student, examination, room and seat"],
                "action_url": f"{reverse('officer_report_export')}?type=allocations",
                "action_label": "Download CSV",
            },
        ]
        count = len(rows)
    elif section == "integrations":
        title = "ERP Integration"
        description = "Integration health must reflect a configured connector and its actual synchronization logs."
        columns = ["Status", "Details"]
        rows = [
            {
                "cells": [
                    "Not configured",
                    "No ERP synchronization connector or job-status model is available in this deployment. "
                    "No retry action is shown; academic data remains managed by existing records and services.",
                ]
            }
        ]
        count = 1
    elif section == "profile":
        title = "Officer Profile"
        description = "Your identity and active role assignments."
        roles = sorted(request.user.role_names)
        columns = ["Name", "Username", "Email", "Active roles"]
        rows = [
            {
                "cells": [
                    request.user.get_full_name() or request.user.username,
                    request.user.username,
                    request.user.email,
                    ", ".join(roles) or ("System Administrator" if request.user.is_superuser else "Examination Officer"),
                ]
            }
        ]
        count = 1
    else:
        return HttpResponseForbidden("This officer workspace is not available.")

    if page_obj is None:
        page_obj = Paginator(rows, 25).get_page(request.GET.get("page"))
    else:
        page_obj.object_list = rows
    return render(
        request,
        "officer/workspace.html",
        {
            "section": section,
            "title": title,
            "description": description,
            "columns": columns,
            "page_obj": page_obj,
            "count": count,
            "search": search,
            "available_examinations": available_examinations,
            "available_rooms": available_rooms,
            "available_lecturers": available_lecturers,
            "available_periods": available_periods,
            "available_campuses": available_campuses,
            "available_buildings": available_buildings,
            "status_filter_options": status_filter_options,
            "planner_period": planner_period,
            "status_filter": status_filter,
            "date_filter": date_filter,
            "allocation_filter": allocation_filter,
            "campus_filter": campus_filter,
            "building_filter": building_filter,
            "capacity_filter": capacity_filter,
        },
    )


@officer_required
@require_POST
def officer_run_reconciliation(request):
    examination_id = request.POST.get("examination_id", "").strip()
    examination = get_object_or_404(Examination, pk=examination_id)
    report = ExamReconciliationEngine(
        examination_id=examination.id,
        generated_by=request.user,
    ).run_reconciliation()
    messages.success(
        request,
        f"Reconciliation completed for {examination.unit.code}: "
        f"{report['total_anomalies']} anomal{('y' if report['total_anomalies'] == 1 else 'ies')} detected.",
    )
    return redirect("officer_workspace", section="reconciliation")


@officer_required
def officer_create_period(request):
    form = ExaminationPeriodForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        period = form.save(commit=False)
        period.created_by = request.user
        period.save()
        messages.success(request, "Examination period created.")
        return redirect("period_timetable_planner", period_id=period.id)
    return render(
        request,
        "officer/form.html",
        {
            "title": "Create examination period",
            "description": "Set the academic term and date range for examination planning.",
            "form": form,
            "cancel_url": reverse("officer_workspace", kwargs={"section": "periods"}),
        },
    )


@officer_required
def officer_edit_period(request, period_id):
    period = get_object_or_404(ExaminationPeriod, pk=period_id)
    if period.status != "DRAFT":
        messages.error(request, "Only draft examination periods can be edited.")
        return redirect("officer_workspace", section="periods")
    form = ExaminationPeriodForm(request.POST or None, instance=period)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Examination period details updated.")
        return redirect("officer_workspace", section="periods")
    return render(
        request,
        "officer/form.html",
        {
            "title": f"Edit {period.name}",
            "description": "Update this period's information. Workflow status changes remain in the timetable workflow.",
            "form": form,
            "cancel_url": reverse("officer_workspace", kwargs={"section": "periods"}),
        },
    )


@officer_required
def officer_create_examination(request):
    form = ExaminationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        examination = form.save(commit=False)
        examination.created_by = request.user
        examination.save()
        messages.success(request, f"Examination for {examination.unit.code} created.")
        return redirect(
            f"{reverse('officer_workspace', kwargs={'section': 'examinations'})}?period={examination.period_id}"
        )
    return render(
        request,
        "officer/form.html",
        {
            "title": "Create examination",
            "description": "Add an examination record to an existing period.",
            "form": form,
            "cancel_url": reverse("officer_workspace", kwargs={"section": "examinations"}),
        },
    )


@officer_required
def officer_edit_examination(request, examination_id):
    examination = get_object_or_404(Examination, pk=examination_id)
    if examination.period.status != "DRAFT":
        messages.error(request, "Examinations in a non-draft period cannot be edited here.")
        return redirect("officer_workspace", section="examinations")
    form = ExaminationForm(request.POST or None, instance=examination)
    if request.method == "POST" and form.is_valid():
        examination = form.save()
        messages.success(request, f"Examination for {examination.unit.code} updated.")
        return redirect(
            f"{reverse('officer_workspace', kwargs={'section': 'examinations'})}?period={examination.period_id}"
        )
    return render(
        request,
        "officer/form.html",
        {
            "title": f"Edit {examination.unit.code} examination",
            "description": "Update examination details. Schedule changes remain subject to timetable validation.",
            "form": form,
            "cancel_url": reverse("officer_workspace", kwargs={"section": "examinations"}),
        },
    )


@officer_required
def officer_room_form(request, room_id=None):
    room = get_object_or_404(Room, pk=room_id) if room_id is not None else None
    form = RoomForm(request.POST or None, instance=room)
    if request.method == "POST" and form.is_valid():
        saved_room = form.save()
        messages.success(
            request,
            f"Room {saved_room.name} was {'updated' if room else 'created'}.",
        )
        return redirect("officer_workspace", section="rooms")
    return render(
        request,
        "officer/form.html",
        {
            "title": f"Edit {room.name}" if room else "Create room",
            "description": (
                "Update the venue details used by examination scheduling."
                if room
                else "Add a venue to the campus room inventory."
            ),
            "form": form,
            "cancel_url": reverse("officer_workspace", kwargs={"section": "rooms"}),
        },
    )


@officer_required
def officer_anomaly_detail(request, anomaly_id):
    anomaly = get_object_or_404(
        ReconciliationAnomaly.objects.select_related(
            "report__examination__unit",
            "report__examination__period",
            "student__user",
            "resolved_by",
        ),
        pk=anomaly_id,
    )
    if request.method == "POST":
        resolution_notes = request.POST.get("resolution_notes", "").strip()
        if anomaly.is_resolved:
            messages.error(request, "This anomaly has already been resolved.")
        elif not resolution_notes:
            messages.error(request, "Add resolution notes before resolving an anomaly.")
        else:
            anomaly.is_resolved = True
            anomaly.resolution_notes = resolution_notes
            anomaly.resolved_by = request.user
            anomaly.resolved_at = timezone.now()
            anomaly.save(
                update_fields=[
                    "is_resolved",
                    "resolution_notes",
                    "resolved_by",
                    "resolved_at",
                ]
            )
            messages.success(request, "Anomaly marked resolved with an audit record.")
            return redirect("officer_anomaly_detail", anomaly_id=anomaly.id)
    return render(request, "officer/anomaly_detail.html", {"anomaly": anomaly})


@officer_required
def officer_case_detail(request, case_id):
    case = get_object_or_404(
        MalpracticeCase.objects.select_related(
            "examination__unit",
            "examination__period",
            "student__user",
            "room__building__campus",
            "reported_by__user",
            "reviewed_by__user",
        ).prefetch_related("evidence_files", "audit_events__actor__user"),
        pk=case_id,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "transition":
                determination = request.POST.get("determination", "").strip()
                if not determination:
                    raise MalpracticeError("A determination or review note is required.")
                MalpracticeService.transition(
                    case,
                    request.user,
                    request.POST.get("status", ""),
                    determination,
                )
                messages.success(request, f"Case {case.case_number} was updated.")
                return redirect("officer_case_detail", case_id=case.id)
            if action == "upload_evidence":
                upload = request.FILES.get("file")
                if not upload:
                    raise MalpracticeError("Select an evidence file to upload.")
                MalpracticeService.add_evidence(
                    case,
                    getattr(request.user, "lecturer_profile", None),
                    upload,
                    request.POST.get("description", "").strip(),
                )
                messages.success(request, "Evidence was uploaded and recorded.")
                return redirect("officer_case_detail", case_id=case.id)
            raise MalpracticeError("Unsupported case action.")
        except MalpracticeError as exc:
            messages.error(request, str(exc))
    allowed_transitions = {
        "REPORTED": ["UNDER_INVESTIGATION", "DISMISS"],
        "UNDER_INVESTIGATION": ["DISMISS", "SANCTIONED"],
    }.get(case.status, [])
    return render(
        request,
        "officer/case_detail.html",
        {
            "case": case,
            "allowed_transitions": allowed_transitions,
        },
    )


@officer_required
def officer_case_evidence(request, evidence_id):
    evidence_record = get_object_or_404(
        MalpracticeEvidence.objects.select_related("case"),
        pk=evidence_id,
    )
    filename = get_valid_filename(evidence_record.file.name.rsplit("/", 1)[-1])
    return FileResponse(
        evidence_record.file.open("rb"),
        as_attachment=True,
        filename=filename or f"case-evidence-{evidence_id}",
    )


@officer_required
def officer_session_detail(request, session_id):
    session = get_object_or_404(
        ExaminationSession.objects.select_related(
            "examination__period", "examination__unit", "room__building__campus"
        ),
        pk=session_id,
    )
    duties = InvigilatorDuty.objects.filter(
        examination=session.examination, room=session.room
    ).select_related("lecturer__user")
    allocations = StudentExamAllocation.objects.filter(
        examination=session.examination, room=session.room
    ).select_related("student__user")
    attendance = ExamAttendance.objects.filter(
        examination=session.examination, room=session.room
    ).select_related("student__user", "recorded_by__user")
    cases = MalpracticeCase.objects.filter(
        examination=session.examination, room=session.room
    ).select_related("student", "reported_by__user")
    return render(
        request,
        "officer/session_detail.html",
        {
            "session": session,
            "duties": duties,
            "allocations": allocations,
            "attendance": attendance,
            "cases": cases,
        },
    )


@officer_required
def officer_report_export(request):
    report_type = request.GET.get("type")
    response = HttpResponse(content_type="text/csv")
    if report_type == "examinations":
        response["Content-Disposition"] = 'attachment; filename="examination-schedule.csv"'
        writer = csv.writer(response)
        writer.writerow(["Unit", "Unit name", "Period", "Exam date", "Start", "End", "Status"])
        for examination in Examination.objects.select_related(
            "unit", "period", "schedule"
        ).order_by("period__start_date", "unit__code"):
            schedule = getattr(examination, "schedule", None)
            writer.writerow(
                [
                    examination.unit.code,
                    examination.unit.name,
                    examination.period.name,
                    schedule.exam_date if schedule else "",
                    schedule.start_time if schedule else "",
                    schedule.end_time if schedule else "",
                    examination.status,
                ]
            )
    elif report_type == "allocations":
        response["Content-Disposition"] = 'attachment; filename="student-exam-allocations.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Student number", "Student name", "Examination", "Period", "Room", "Seat", "Status"]
        )
        for allocation in StudentExamAllocation.objects.select_related(
            "student__user", "examination__unit", "examination__period", "room"
        ).order_by("examination__period__start_date", "student__registration_number"):
            writer.writerow(
                [
                    allocation.student.registration_number,
                    allocation.student.user.get_full_name(),
                    allocation.examination.unit.code,
                    allocation.examination.period.name,
                    allocation.room.name,
                    allocation.seat_number or "",
                    allocation.status,
                ]
            )
    else:
        return HttpResponseForbidden("Select a supported report type.")
    return response
