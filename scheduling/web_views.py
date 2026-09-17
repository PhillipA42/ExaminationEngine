from datetime import datetime, date, time
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.db import transaction

from academics.models import School, Department, Course, Unit, Student, UnitRegistration
from locations.models import Room, Campus, Building
from .models import (
    ExaminationPeriod, Examination, ExamSchedule,
    ExamRoomAllocation, StudentExamAllocation, ExamTimeSlot
)
from .engine import TimetableSchedulerEngine
from .services import ExamConstraintChecker, ConflictReport


def officer_or_admin_required(view_func):
    """Ensures the user is an Examination Officer, Timetabler, or Administrator."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/invigilator/login/?next={request.path}")
        if request.user.is_superuser or getattr(request.user, 'is_exam_officer', False):
            return view_func(request, *args, **kwargs)
        user_roles = set(request.user.user_roles.filter(status='ACTIVE').values_list('role__name', flat=True)) if hasattr(request.user, 'user_roles') else set()
        if not (user_roles & {'EXAM_OFFICER', 'ADMIN'}):
            return HttpResponseForbidden("Access restricted: Examination Officer / Timetabler authorization required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


@officer_or_admin_required
def officer_planning_dashboard(request):
    """
    Overview of all examination periods with quick status metrics and planning actions.
    """
    periods = ExaminationPeriod.objects.all().order_by('-start_date')
    total_periods = periods.count()
    active_periods = periods.filter(status__in=['DRAFT', 'GENERATING', 'PUBLISHED']).count()
    published_periods = periods.filter(status='PUBLISHED').count()
    total_rooms = Room.objects.filter(status='ACTIVE').count()

    return render(request, 'scheduling/period_list.html', {
        'periods': periods,
        'total_periods': total_periods,
        'active_periods': active_periods,
        'published_periods': published_periods,
        'total_rooms': total_rooms,
    })


@officer_or_admin_required
def period_timetable_planner(request, period_id):
    """
    Interactive Timetable & Room Planner for a specific Examination Period:
    - Triggers automated constraint-satisfaction generation
    - Audits hard and soft constraints with Conflict Inspector
    - Publishes verified timetables
    - Filterable schedule matrix by date, department, school, and room
    """
    period = get_object_or_404(ExaminationPeriod, id=period_id)

    # 1. Handle Generation Action
    if request.method == 'POST' and 'action_generate' in request.POST:
        scheduler = TimetableSchedulerEngine(period_id=period.id)
        result = scheduler.execute_scheduling()

        if result['success']:
            messages.success(
                request,
                f"Successfully generated conflict-free timetable: {result['scheduled_count']} examinations scheduled "
                f"across {result['days_utilized']} days using {result['total_rooms_used']} rooms."
            )
        else:
            errors_preview = [v.message for v in result['report'].hard_violations[:3]]
            error_str = " | ".join(errors_preview)
            messages.error(
                request,
                f"Generation halted to prevent invalid scheduling: {error_str}"
            )
        return redirect('period_timetable_planner', period_id=period.id)

    # 2. Handle Constraint Audit Action
    audit_report = None
    if request.method == 'POST' and 'action_validate' in request.POST:
        audit_report = ExamConstraintChecker.validate_period_entire_timetable(period)
        if audit_report.is_valid:
            messages.success(request, "Audit passed 100%: Zero hard-constraint violations detected.")
        else:
            messages.warning(
                request,
                f"Audit detected {len(audit_report.hard_violations)} hard conflict(s). Review conflict details below."
            )

    # 3. Handle Publication Action
    if request.method == 'POST' and 'action_publish' in request.POST:
        success, pub_report = ExamConstraintChecker.publish_timetable(period, request.user)
        if success:
            messages.success(request, f"Examination timetable for {period.name} has been officially PUBLISHED.")
        else:
            messages.error(
                request,
                f"Cannot publish timetable: {len(pub_report.hard_violations)} hard conflicts must be resolved first."
            )
            audit_report = pub_report
        return redirect('period_timetable_planner', period_id=period.id)

    # 4. Fetch schedules with filtering
    schedules_qs = ExamSchedule.objects.filter(examination__period=period).select_related(
        'examination__unit__course__department__school',
        'examination__period'
    ).prefetch_related('examination__room_allocations__room').order_by('exam_date', 'start_time')

    # Apply Filters
    filter_date = request.GET.get('date', '').strip()
    if filter_date:
        schedules_qs = schedules_qs.filter(exam_date=filter_date)

    filter_dept = request.GET.get('department', '').strip()
    if filter_dept:
        schedules_qs = schedules_qs.filter(examination__unit__course__department_id=filter_dept)

    filter_school = request.GET.get('school', '').strip()
    if filter_school:
        schedules_qs = schedules_qs.filter(examination__unit__course__department__school_id=filter_school)

    filter_room = request.GET.get('room', '').strip()
    if filter_room:
        schedules_qs = schedules_qs.filter(examination__room_allocations__room_id=filter_room).distinct()

    # Compute Summary KPIs
    total_examinations = period.examinations.count()
    scheduled_count = period.scheduled_examinations_count
    unscheduled_count = total_examinations - scheduled_count

    # If not audited yet, run a passive check to detect status
    if audit_report is None:
        audit_report = ExamConstraintChecker.validate_period_entire_timetable(period)

    # Filter Options
    schools = School.objects.all().order_by('name')
    departments = Department.objects.all().order_by('name')
    rooms = Room.objects.filter(status='ACTIVE').order_by('name')
    time_slots = period.time_slots.filter(is_active=True).order_by('start_time')

    # Build rich schedule items
    schedule_items = []
    for s in schedules_qs:
        exam = s.examination
        rooms_allocated = exam.room_allocations.all()
        total_room_capacity = sum(r.allocated_capacity for r in rooms_allocated)
        reg_count = exam.registered_students_count
        alloc_count = exam.allocated_students_count

        schedule_items.append({
            'schedule': s,
            'examination': exam,
            'unit': exam.unit,
            'rooms_allocated': rooms_allocated,
            'total_room_capacity': total_room_capacity,
            'reg_count': reg_count,
            'alloc_count': alloc_count,
            'is_capacity_clean': total_room_capacity >= reg_count,
        })

    return render(request, 'scheduling/timetable_planner.html', {
        'period': period,
        'schedule_items': schedule_items,
        'total_examinations': total_examinations,
        'scheduled_count': scheduled_count,
        'unscheduled_count': unscheduled_count,
        'audit_report': audit_report,
        'schools': schools,
        'departments': departments,
        'rooms': rooms,
        'time_slots': time_slots,
        'filter_date': filter_date,
        'filter_dept': filter_dept,
        'filter_school': filter_school,
        'filter_room': filter_room,
    })


@officer_or_admin_required
@transaction.atomic
def manual_schedule_exam(request, examination_id):
    """
    Manual Examination Scheduling & Rescheduling with Strict Real-Time Constraint Enforcement.
    Admins can manually allocate date, time, and rooms, but the engine guarantees rules cannot be bypassed.
    """
    examination = get_object_or_404(
        Examination.objects.select_related('unit__course__department__school', 'period'),
        id=examination_id
    )
    period = examination.period
    existing_schedule = getattr(examination, 'schedule', None)
    active_rooms = Room.objects.filter(status='ACTIVE').order_by('name')
    existing_allocations = {ra.room_id: ra.allocated_capacity for ra in examination.room_allocations.all()}

    validation_errors = []

    if request.method == 'POST':
        exam_date_str = request.POST.get('exam_date', '').strip()
        start_time_str = request.POST.get('start_time', '').strip()
        end_time_str = request.POST.get('end_time', '').strip()
        selected_room_ids = request.POST.getlist('rooms')

        try:
            exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()
            start_time = datetime.strptime(start_time_str, '%H:%M').time()
            end_time = datetime.strptime(end_time_str, '%H:%M').time()
        except ValueError:
            validation_errors.append("Invalid date or time format. Please provide YYYY-MM-DD and HH:MM.")
            exam_date = None

        if exam_date:
            # Construct room allocations data
            room_allocations_data = []
            for rid in selected_room_ids:
                try:
                    r_obj = Room.objects.get(id=rid)
                    cap_val = int(request.POST.get(f'capacity_{rid}', r_obj.capacity))
                    room_allocations_data.append({'room': r_obj, 'allocated_capacity': cap_val})
                except (Room.DoesNotExist, ValueError):
                    pass

            # Run strict constraint check
            report = ExamConstraintChecker.validate_manual_schedule(
                examination=examination,
                exam_date=exam_date,
                start_time=start_time,
                end_time=end_time,
                room_allocations=room_allocations_data,
                exclude_current=True
            )

            if report.is_valid:
                # Save schedule
                if existing_schedule:
                    existing_schedule.exam_date = exam_date
                    existing_schedule.start_time = start_time
                    existing_schedule.end_time = end_time
                    existing_schedule.status = 'SCHEDULED'
                    existing_schedule.save()
                else:
                    existing_schedule = ExamSchedule.objects.create(
                        examination=examination,
                        exam_date=exam_date,
                        start_time=start_time,
                        end_time=end_time,
                        status='SCHEDULED'
                    )

                # Reallocate rooms & partition students
                examination.room_allocations.all().delete()
                examination.student_allocations.all().delete()

                registered_students = list(
                    UnitRegistration.objects.filter(
                        unit=examination.unit,
                        academic_year=period.academic_year,
                        semester=period.semester,
                        registration_status='REGISTERED'
                    ).values_list('student_id', flat=True).order_by('student__registration_number')
                )

                rem = registered_students.copy()
                for item in room_allocations_data:
                    rm = item['room']
                    cap = item['allocated_capacity']
                    ExamRoomAllocation.objects.create(
                        examination=examination,
                        room=rm,
                        allocated_capacity=cap
                    )
                    slice_studs = rem[:cap]
                    rem = rem[cap:]
                    StudentExamAllocation.objects.bulk_create([
                        StudentExamAllocation(
                            examination=examination,
                            student_id=sid,
                            room=rm,
                            seat_number='Pending On-Site',
                            status='ALLOCATED'
                        )
                        for sid in slice_studs
                    ])

                examination.status = 'SCHEDULED'
                examination.save()

                messages.success(request, f"Successfully scheduled {examination.unit.code} for {exam_date} ({start_time} - {end_time}).")
                return redirect('period_timetable_planner', period_id=period.id)
            else:
                validation_errors = [v.message for v in report.hard_violations]

    return render(request, 'scheduling/manual_schedule.html', {
        'examination': examination,
        'period': period,
        'existing_schedule': existing_schedule,
        'active_rooms': active_rooms,
        'existing_allocations': existing_allocations,
        'validation_errors': validation_errors,
    })


@officer_or_admin_required
def period_time_slots(request, period_id):
    """
    Manage customizable time slots (sessions) for the examination period.
    """
    period = get_object_or_404(ExaminationPeriod, id=period_id)
    slots = period.time_slots.all().order_by('start_time')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip() or 'Examination Session'
        start_str = request.POST.get('start_time', '').strip()
        end_str = request.POST.get('end_time', '').strip()

        try:
            s_time = datetime.strptime(start_str, '%H:%M').time()
            e_time = datetime.strptime(end_str, '%H:%M').time()
            if s_time >= e_time:
                messages.error(request, "Start time must be before end time.")
            else:
                ExamTimeSlot.objects.create(
                    period=period,
                    name=name,
                    start_time=s_time,
                    end_time=e_time,
                    is_active=True
                )
                messages.success(request, f"Added time slot: {name} ({start_str} - {end_str}).")
                return redirect('period_time_slots', period_id=period.id)
        except ValueError:
            messages.error(request, "Invalid time format. Please use HH:MM.")

    return render(request, 'scheduling/time_slots.html', {
        'period': period,
        'slots': slots,
    })


def master_timetable_print(request, period_id):
    """
    Official University Printable Master Timetable View.
    Accessible to authorized staff and students for noticeboard and distribution purposes.
    """
    period = get_object_or_404(ExaminationPeriod, id=period_id)
    schedules = ExamSchedule.objects.filter(examination__period=period).select_related(
        'examination__unit__course__department__school',
        'examination__period'
    ).prefetch_related('examination__room_allocations__room').order_by('exam_date', 'start_time')

    # Group by Date
    grouped_by_date = {}
    for s in schedules:
        d = s.exam_date
        if d not in grouped_by_date:
            grouped_by_date[d] = []
        grouped_by_date[d].append(s)

    sorted_dates = sorted(grouped_by_date.keys())
    date_schedule_list = [(d, grouped_by_date[d]) for d in sorted_dates]

    return render(request, 'scheduling/master_timetable_print.html', {
        'period': period,
        'date_schedule_list': date_schedule_list,
        'total_exams': schedules.count(),
        'generated_date': date.today(),
    })
