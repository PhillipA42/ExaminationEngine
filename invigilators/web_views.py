import json
from datetime import date
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.db import IntegrityError, transaction

from academics.models import Student, Lecturer
from locations.models import Room
from scheduling.models import StudentExamAllocation, ExamSchedule, ExamRoomAllocation
from malpractice.models import MalpracticeCase, MalpracticeEvidence
from .models import InvigilatorDuty, ExamAttendance


def lecturer_required(view_func):
    """Decorator to ensure the authenticated user has an active Lecturer profile."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/invigilator/login/?next={request.path}")
        if not hasattr(request.user, 'lecturer_profile') and not request.user.is_superuser:
            return HttpResponseForbidden("Access restricted: You must have a registered Lecturer profile.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def invigilator_login(request):
    """Invigilator login view."""
    if request.user.is_authenticated and hasattr(request.user, 'lecturer_profile'):
        return redirect('invigilator_dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not hasattr(user, 'lecturer_profile') and not user.is_superuser:
                messages.error(request, "Account found, but no Lecturer profile is associated with this username.")
            else:
                login(request, user)
                messages.success(request, f"Welcome back, {user.get_full_name() or user.username}!")
                next_url = request.POST.get('next') or request.GET.get('next') or 'invigilator_dashboard'
                return redirect(next_url)
        else:
            messages.error(request, "Invalid username or password. Please try again.")

    return render(request, 'invigilators/login.html')


def invigilator_logout(request):
    """Invigilator logout view."""
    logout(request)
    messages.info(request, "You have been logged out safely.")
    return redirect('invigilator_login')


@lecturer_required
def invigilator_dashboard(request):
    """Dashboard displaying all assigned examination rooms and duties for the logged-in lecturer."""
    lecturer = getattr(request.user, 'lecturer_profile', None)
    
    if lecturer:
        duties_qs = InvigilatorDuty.objects.filter(lecturer=lecturer).select_related(
            'examination__unit__course',
            'examination__period',
            'room__building__campus'
        ).order_by('examination__schedule__exam_date', 'examination__schedule__start_time')
    else:
        duties_qs = InvigilatorDuty.objects.all().select_related(
            'examination__unit__course',
            'examination__period',
            'room__building__campus'
        ).order_by('examination__schedule__exam_date', 'examination__schedule__start_time')

    today = date.today()

    # Calculate summary counts
    duties_list = []
    total_duties = duties_qs.count()
    upcoming_count = 0
    completed_count = 0

    for duty in duties_qs:
        schedule = getattr(duty.examination, 'schedule', None)
        # Compute student count allocated to this specific room
        student_count = StudentExamAllocation.objects.filter(
            examination=duty.examination,
            room=duty.room
        ).count()

        # Compute attendance count
        checked_in_count = ExamAttendance.objects.filter(
            examination=duty.examination,
            room=duty.room,
            is_present=True
        ).count()

        is_today = False
        is_upcoming = False
        if schedule and schedule.exam_date:
            if schedule.exam_date == today:
                is_today = True
                upcoming_count += 1
            elif schedule.exam_date > today:
                is_upcoming = True
                upcoming_count += 1
            else:
                completed_count += 1

        duties_list.append({
            'duty': duty,
            'schedule': schedule,
            'student_count': student_count,
            'checked_in_count': checked_in_count,
            'is_today': is_today,
            'is_upcoming': is_upcoming,
        })

    context = {
        'lecturer': lecturer,
        'duties_list': duties_list,
        'total_duties': total_duties,
        'upcoming_count': upcoming_count,
        'completed_count': completed_count,
        'today': today,
    }
    return render(request, 'invigilators/dashboard.html', context)


@lecturer_required
def session_roster(request, duty_id):
    """Session student roster and physical booklet check-in page for a specific assigned duty."""
    lecturer = getattr(request.user, 'lecturer_profile', None)
    
    if lecturer:
        duty = get_object_or_404(
            InvigilatorDuty.objects.select_related(
                'examination__unit__course__department__school',
                'examination__period',
                'room__building__campus'
            ),
            id=duty_id,
            lecturer=lecturer
        )
    else:
        duty = get_object_or_404(
            InvigilatorDuty.objects.select_related(
                'examination__unit__course__department__school',
                'examination__period',
                'room__building__campus'
            ),
            id=duty_id
        )

    examination = duty.examination
    room = duty.room
    schedule = getattr(examination, 'schedule', None)

    # Fetch all allocated students for this exam session in this room
    allocations = StudentExamAllocation.objects.filter(
        examination=examination,
        room=room
    ).select_related('student__user', 'student__course', 'student__department').order_by('student__registration_number')

    # Fetch all recorded attendance entries for this exam
    attendance_records = ExamAttendance.objects.filter(
        examination=examination,
        room=room
    ).select_related('recorded_by__user')
    attendance_map = {att.student_id: att for att in attendance_records}

    # Fetch malpractice cases reported for this exam session
    session_malpractice_cases = MalpracticeCase.objects.filter(
        examination=examination,
        room=room
    ).select_related('student__user', 'reported_by__user').prefetch_related('evidence_files').order_by('-reported_at')

    roster_items = []
    checked_in_count = 0
    absent_count = 0
    pending_count = 0

    for alloc in allocations:
        student = alloc.student
        attendance = attendance_map.get(student.id)

        if attendance:
            if attendance.is_present:
                status = 'CHECKED_IN'
                checked_in_count += 1
            else:
                status = 'ABSENT'
                absent_count += 1
        else:
            status = 'PENDING'
            pending_count += 1

        # Check if student has malpractice reported
        has_malpractice = session_malpractice_cases.filter(student=student).exists()

        roster_items.append({
            'student': student,
            'seat_number': alloc.seat_number,
            'attendance': attendance,
            'status': status,
            'has_malpractice': has_malpractice,
        })

    total_allocated = len(roster_items)
    checkin_percent = int((checked_in_count / total_allocated * 100)) if total_allocated > 0 else 0

    # Available rooms for selection in malpractice modal
    allocated_rooms = Room.objects.filter(allocated_exams__examination=examination).distinct()
    if not allocated_rooms.exists():
        allocated_rooms = [room]

    incident_types = [
        "Unauthorized Material (Cheat Notes/Formulas)",
        "Unauthorized Electronic Device (Phone/Smartwatch)",
        "Impersonation / Proxy Candidate",
        "Unauthorized Communication / Talking",
        "Refusal to Surrender Booklet",
        "Disruptive Hall Behavior",
        "Other Examination Irregularity",
    ]

    severity_choices = [
        ('LOW', 'Low - Minor irregularity'),
        ('MEDIUM', 'Medium - Unauthorized materials present'),
        ('HIGH', 'High - Active cheating / unauthorized device'),
        ('CRITICAL', 'Critical - Impersonation / severe disruption'),
    ]

    context = {
        'duty': duty,
        'examination': examination,
        'room': room,
        'schedule': schedule,
        'roster_items': roster_items,
        'total_allocated': total_allocated,
        'checked_in_count': checked_in_count,
        'absent_count': absent_count,
        'pending_count': pending_count,
        'checkin_percent': checkin_percent,
        'allocated_rooms': allocated_rooms,
        'incident_types': incident_types,
        'severity_choices': severity_choices,
        'session_malpractice_cases': session_malpractice_cases,
        'malpractice_count': session_malpractice_cases.count(),
    }
    return render(request, 'invigilators/session_roster.html', context)


@lecturer_required
@require_POST
def student_checkin(request, duty_id):
    """
    Handles student check-in, presence toggle, and booklet serial number capture.
    Accepts both JSON AJAX payloads and standard Form POST data.
    """
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if lecturer:
        duty = get_object_or_404(InvigilatorDuty, id=duty_id, lecturer=lecturer)
    else:
        duty = get_object_or_404(InvigilatorDuty, id=duty_id)

    # Determine if request is JSON or Form data
    is_json = request.content_type == 'application/json'
    if is_json:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid JSON format.'}, status=400)
    else:
        data = request.POST

    student_id = data.get('student_id')
    booklet_serial_number = data.get('booklet_serial_number', '').strip()
    is_present = data.get('is_present') in [True, 'true', 'True', '1', 'on']
    remarks = data.get('remarks', '').strip()

    if not student_id:
        msg = "Student ID is required."
        if is_json:
            return JsonResponse({'success': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('session_roster', duty_id=duty.id)

    student = get_object_or_404(Student, id=student_id)

    # If marked present, booklet serial number is required
    if is_present and not booklet_serial_number:
        msg = "Physical booklet serial number is required to check in a present student."
        if is_json:
            return JsonResponse({'success': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('session_roster', duty_id=duty.id)

    # If absent and no booklet serial number was supplied, generate unique absent placeholder
    if not is_present and not booklet_serial_number:
        booklet_serial_number = f"ABSENT-EX{duty.examination_id}-ST{student.id}"

    # Check for duplicate booklet serial number across other students
    existing_serial = ExamAttendance.objects.filter(booklet_serial_number=booklet_serial_number).exclude(
        examination=duty.examination,
        student=student
    ).first()

    if existing_serial:
        msg = (
            f"Booklet serial number '{booklet_serial_number}' is already assigned to "
            f"{existing_serial.student.registration_number} in {existing_serial.examination.unit.code}!"
        )
        if is_json:
            return JsonResponse({'success': False, 'message': msg}, status=409)
        messages.error(request, msg)
        return redirect('session_roster', duty_id=duty.id)

    # Persist or update attendance record
    try:
        with transaction.atomic():
            attendance, created = ExamAttendance.objects.update_or_create(
                examination=duty.examination,
                student=student,
                defaults={
                    'room': duty.room,
                    'recorded_by': lecturer,
                    'booklet_serial_number': booklet_serial_number,
                    'is_present': is_present,
                    'remarks': remarks
                }
            )

        success_msg = f"Check-in updated for {student.registration_number} (Booklet #{booklet_serial_number})."
        
        if is_json:
            # Calculate updated session statistics
            total_allocated = StudentExamAllocation.objects.filter(examination=duty.examination, room=duty.room).count()
            checked_in_count = ExamAttendance.objects.filter(examination=duty.examination, room=duty.room, is_present=True).count()
            absent_count = ExamAttendance.objects.filter(examination=duty.examination, room=duty.room, is_present=False).count()
            pending_count = max(0, total_allocated - (checked_in_count + absent_count))
            checkin_percent = int((checked_in_count / total_allocated * 100)) if total_allocated > 0 else 0

            return JsonResponse({
                'success': True,
                'message': success_msg,
                'student_id': student.id,
                'registration_number': student.registration_number,
                'student_name': student.user.get_full_name() or student.user.username,
                'booklet_serial_number': booklet_serial_number,
                'is_present': is_present,
                'remarks': remarks,
                'status': 'CHECKED_IN' if is_present else 'ABSENT',
                'stats': {
                    'total': total_allocated,
                    'checked_in': checked_in_count,
                    'absent': absent_count,
                    'pending': pending_count,
                    'percent': checkin_percent
                }
            })

        messages.success(request, success_msg)
        return redirect('session_roster', duty_id=duty.id)

    except IntegrityError as e:
        msg = f"Database constraint error: {str(e)}"
        if is_json:
            return JsonResponse({'success': False, 'message': msg}, status=500)
        messages.error(request, msg)
        return redirect('session_roster', duty_id=duty.id)


@lecturer_required
@require_POST
def file_malpractice_report(request, duty_id):
    """
    Handles filing an academic malpractice case during an active exam.
    Captures student, room, incident type, severity, detailed description, and evidence attachments.
    """
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if lecturer:
        duty = get_object_or_404(InvigilatorDuty, id=duty_id, lecturer=lecturer)
    else:
        duty = get_object_or_404(InvigilatorDuty, id=duty_id)

    student_id = request.POST.get('student_id')
    room_id = request.POST.get('room_id') or duty.room_id
    incident_type = request.POST.get('incident_type', '').strip()
    severity = request.POST.get('severity', 'MEDIUM').strip()
    description = request.POST.get('description', '').strip()
    evidence_file = request.FILES.get('evidence_file')

    # Basic validations
    if not student_id:
        messages.error(request, "Please select the implicated student.")
        return redirect('session_roster', duty_id=duty.id)
    if not incident_type:
        messages.error(request, "Please specify the incident type.")
        return redirect('session_roster', duty_id=duty.id)
    if not description:
        messages.error(request, "Please provide a detailed description of the observed malpractice incident.")
        return redirect('session_roster', duty_id=duty.id)

    student = get_object_or_404(Student, id=student_id)
    room = get_object_or_404(Room, id=room_id)

    # Generate unique case number
    count = MalpracticeCase.objects.count() + 1
    case_number = f"MAL-2026-{count:05d}"
    while MalpracticeCase.objects.filter(case_number=case_number).exists():
        count += 1
        case_number = f"MAL-2026-{count:05d}"

    try:
        with transaction.atomic():
            case = MalpracticeCase.objects.create(
                case_number=case_number,
                examination=duty.examination,
                student=student,
                room=room,
                reported_by=lecturer,
                incident_type=incident_type,
                severity=severity,
                description=description,
                status='REPORTED'
            )

            if evidence_file:
                MalpracticeEvidence.objects.create(
                    case=case,
                    file=evidence_file,
                    description=f"Evidence uploaded on-site by {request.user.get_full_name() or request.user.username}"
                )

        msg = f"Malpractice Report #{case.case_number} successfully filed for candidate {student.registration_number} ({incident_type})."
        
        # Check if AJAX or multipart AJAX
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
            return JsonResponse({
                'success': True,
                'message': msg,
                'case_number': case.case_number,
                'student_id': student.id,
                'student_name': student.user.get_full_name() or student.user.username,
                'registration_number': student.registration_number,
                'incident_type': incident_type,
                'severity': severity,
                'has_evidence': bool(evidence_file),
            })

        messages.success(request, msg)
        return redirect('session_roster', duty_id=duty.id)

    except Exception as e:
        err_msg = f"Failed to record malpractice case: {str(e)}"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': err_msg}, status=500)
        messages.error(request, err_msg)
        return redirect('session_roster', duty_id=duty.id)
