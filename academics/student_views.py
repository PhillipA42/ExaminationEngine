from datetime import date
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.core import signing
from django.utils import timezone
from urllib.parse import urlencode

from academics.models import Student
from scheduling.models import StudentExamAllocation, ExaminationPeriod


def get_student_allocations(student):
    """Return the student's published examination allocations in schedule order."""
    return StudentExamAllocation.objects.filter(
        student=student, examination__period__status='PUBLISHED'
    ).select_related(
        'examination__unit__course',
        'examination__period',
        'room__building__campus',
        'room__floor',
        'examination__schedule',
    ).order_by('examination__schedule__exam_date', 'examination__schedule__start_time')


def get_next_exam_summary(student):
    allocations = list(get_student_allocations(student))
    today = timezone.localdate()
    next_exam = None
    upcoming = []

    for alloc in allocations:
        schedule = getattr(alloc.examination, 'schedule', None)
        if not schedule or not schedule.exam_date:
            continue
        item = {'allocation': alloc, 'exam': alloc.examination, 'schedule': schedule, 'room': alloc.room}
        if schedule.exam_date >= today:
            upcoming.append(item)
            if next_exam is None:
                next_exam = item

    return {
        'allocations': allocations,
        'next_exam': next_exam,
        'upcoming': upcoming[:5],
        'total': len(allocations),
        'today': today,
    }


def student_required(view_func):
    """Decorator to ensure the authenticated user has an active Student profile."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/student/login/?next={request.path}")
        if not hasattr(request.user, 'student_profile') and not request.user.is_superuser:
            if hasattr(request.user, 'lecturer_profile'):
                messages.info(request, "This account is registered for the Lecturer Portal. Redirected to your lecturer dashboard.")
                return redirect('invigilator_dashboard')
            messages.error(request, "This account does not have a registered Student profile. Please sign in with a student account.")
            return redirect('student_login')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def student_login(request):
    """Backward-compatible redirect to the single credential-based login page."""
    next_url = request.GET.get('next') or request.POST.get('next')
    target = f"/login/?next={next_url}" if next_url else '/login/'
    return redirect(target)


def student_logout(request):
    """Student portal logout view."""
    logout(request)
    messages.info(request, "You have been logged out safely.")
    return redirect('student_login')


@student_required
def student_dashboard(request):
    """Personalized student landing page with the most relevant examination information first."""
    student = getattr(request.user, 'student_profile', None)
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    summary = get_next_exam_summary(student)
    allocations = summary['allocations']
    next_exam = summary['next_exam']
    upcoming = summary['upcoming']

    active_period = allocations[0].examination.period if allocations else ExaminationPeriod.objects.first()
    eligibility_status = 'Eligible'
    eligibility_detail = 'You are cleared to sit this examination.'
    if active_period:
        try:
            from academics.eligibility_services import EligibilityService
            evaluation = EligibilityService().evaluate(student, active_period)
            eligibility_status = getattr(evaluation, 'overall_status', 'Eligible')
            eligibility_detail = getattr(evaluation, 'details', None) or getattr(evaluation, 'reason', None) or 'You are cleared to sit this examination.'
        except Exception:
            pass

    from academics.models import Notification
    recent_updates = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:4]

    context = {
        'student': student,
        'next_exam': next_exam,
        'upcoming_exams': upcoming,
        'total_exams': len(allocations),
        'eligibility_status': eligibility_status,
        'eligibility_detail': eligibility_detail,
        'active_period': active_period,
        'today': summary['today'],
        'recent_updates': recent_updates,
    }
    return render(request, 'student/dashboard.html', context)


@student_required
def student_profile(request):
    """Read-only student profile synchronized from the authoritative ERP data."""
    student = getattr(request.user, 'student_profile', None)
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    context = {
        'student': student,
        'academic_status': getattr(student, 'status', 'ACTIVE'),
    }
    return render(request, 'student/profile.html', context)


@student_required
def student_timetable(request):
    """Personalized timetable dashboard for the logged-in student."""
    student = getattr(request.user, 'student_profile', None)
    
    # If superuser without student profile, pick first student for testing
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    today = timezone.localdate()

    # Fetch all exam allocations for this student with schedule & room details
    allocations_qs = get_student_allocations(student)

    timetable_items = []
    total_exams = allocations_qs.count()
    upcoming_count = 0
    completed_count = 0
    today_count = 0
    next_exam = None

    for alloc in allocations_qs:
        exam = alloc.examination
        schedule = getattr(exam, 'schedule', None)
        
        is_today = False
        is_upcoming = False
        is_past = False

        if schedule and schedule.exam_date:
            if schedule.exam_date == today:
                is_today = True
                today_count += 1
                upcoming_count += 1
            elif schedule.exam_date > today:
                is_upcoming = True
                upcoming_count += 1
            else:
                is_past = True
                completed_count += 1

            if (is_today or is_upcoming) and next_exam is None:
                next_exam = {
                    'alloc': alloc,
                    'schedule': schedule,
                    'is_today': is_today,
                }

        timetable_items.append({
            'allocation': alloc,
            'exam': exam,
            'unit': exam.unit,
            'schedule': schedule,
            'room': alloc.room,
            'seat_number': alloc.seat_number,
            'is_today': is_today,
            'is_upcoming': is_upcoming,
            'is_past': is_past,
        })

    # Latest active exam period name
    active_period = timetable_items[0]['exam'].period if timetable_items else ExaminationPeriod.objects.first()

    context = {
        'student': student,
        'timetable_items': timetable_items,
        'total_exams': total_exams,
        'upcoming_count': upcoming_count,
        'completed_count': completed_count,
        'today_count': today_count,
        'next_exam': next_exam,
        'active_period': active_period,
        'today': today,
    }
    return render(request, 'student/timetable.html', context)


@student_required
def student_exam_detail(request, allocation_id):
    """Central detail page for a single examination allocation."""
    student = getattr(request.user, 'student_profile', None)
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    allocation = get_object_or_404(StudentExamAllocation, id=allocation_id, student=student)
    exam = allocation.examination
    schedule = getattr(exam, 'schedule', None)
    try:
        from academics.eligibility_services import EligibilityService
        eligibility = EligibilityService().evaluate(student, exam.period)
    except Exception:
        eligibility = None

    context = {
        'student': student,
        'allocation': allocation,
        'exam': exam,
        'schedule': schedule,
        'eligibility': eligibility,
        'invigilator': getattr(exam, 'invigilator', None),
        'room': allocation.room,
    }
    return render(request, 'student/exam_detail.html', context)


@student_required
def student_venue_navigation(request, allocation_id):
    """Outdoor-first venue navigation page for the selected examination."""
    student = getattr(request.user, 'student_profile', None)
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    allocation = get_object_or_404(StudentExamAllocation, id=allocation_id, student=student)
    room = allocation.room
    building = room.building
    campus = building.campus
    if building.latitude is not None and building.longitude is not None:
        navigation_destination = f'{building.latitude},{building.longitude}'
    else:
        navigation_destination = f'{building.name}, {campus.name}'
    maps_url = 'https://www.google.com/maps/dir/?' + urlencode({
        'api': '1',
        'destination': navigation_destination,
        'travelmode': 'walking',
    })
    context = {
        'student': student,
        'allocation': allocation,
        'room': room,
        'building': building,
        'campus': campus,
        'maps_url': maps_url,
        'navigation_destination': navigation_destination,
    }
    return render(request, 'student/venue_navigation.html', context)


@student_required
def student_exam_pass(request):
    """Printable Examination Pass / Exam Card for the logged-in student.
    Dynamically embeds the student's eligibility status from EligibilityService."""
    from academics.eligibility_services import EligibilityService

    student = getattr(request.user, 'student_profile', None)
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    # Fetch all exam allocations for the active semester
    allocations = StudentExamAllocation.objects.filter(
        student=student, examination__period__status='PUBLISHED'
    ).select_related(
        'examination__unit__course',
        'examination__period',
        'room__building__campus',
        'room__floor'
    ).order_by('examination__schedule__exam_date', 'examination__schedule__start_time')

    active_period = allocations.first().examination.period if allocations.exists() else None

    # ── Eligibility evaluation ──────────────────────────────────────────
    eligibility_result = None
    if active_period:
        try:
            svc = EligibilityService()
            eligibility_result = svc.evaluate(student, active_period)
        except Exception:
            # Never let eligibility errors block exam-pass generation
            pass

    # Signed token deliberately contains no personal data.  Verification always
    # re-queries authoritative allocations, so timetable changes invalidate it.
    pass_token = signing.dumps({
        'student_id': student.id,
        'period_id': active_period.id if active_period else None,
        'allocation_ids': list(allocations.values_list('id', flat=True)),
    }, salt='exam-pass')
    verification_code = f"PASS-{signing.loads(pass_token, salt='exam-pass')['student_id']}-{active_period.id if active_period else 'NONE'}"

    context = {
        'student': student,
        'allocations': allocations,
        'active_period': active_period,
        'verification_code': verification_code,
        'pass_token': pass_token,
        'generation_date': date.today(),
        'eligibility': eligibility_result,
    }
    return render(request, 'student/exam_pass.html', context)


def exam_pass_verification(request):
    """Staff-only pass validation; never exposes marks, evidence, or other data."""
    if not request.user.is_authenticated:
        return redirect(f"/invigilator/login/?next={request.path}")
    roles = set(request.user.user_roles.filter(status='ACTIVE').values_list('role__name', flat=True)) if hasattr(request.user, 'user_roles') else set()
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if not (request.user.is_superuser or lecturer or roles & {'EXAM_OFFICER', 'ADMIN'}):
        return HttpResponseForbidden('Authorized examination personnel only.')
    token = request.GET.get('token', '')
    result = None
    try:
        data = signing.loads(token, salt='exam-pass', max_age=60 * 60 * 24 * 30)
        allocations = StudentExamAllocation.objects.filter(
            student_id=data['student_id'], examination__period_id=data['period_id'],
            examination__period__status='PUBLISHED', id__in=data['allocation_ids']
        ).select_related('student__user', 'examination__unit', 'room__building', 'room__floor')
        if lecturer:
            allocations = allocations.filter(examination__invigilator_duties__lecturer=lecturer)
        result = {'valid': allocations.exists(), 'allocations': allocations, 'student': allocations.first().student if allocations.exists() else None}
    except (signing.BadSignature, signing.SignatureExpired, KeyError):
        result = {'valid': False, 'allocations': []}
    return render(request, 'student/pass_verify.html', {'result': result})


@student_required
def student_notifications(request):
    """Student notification inbox view."""
    from academics.models import Notification
    from academics.notification_services import NotificationService

    notifications = Notification.objects.filter(
        recipient=request.user
    ).prefetch_related('deliveries').order_by('-created_at')[:50]

    svc = NotificationService()
    unread_count = svc.get_unread_count(request.user)

    # Mark all as read when the page is opened
    svc.mark_all_read(request.user)

    context = {
        'notifications': notifications,
        'unread_count': unread_count,
    }
    return render(request, 'student/notifications.html', context)


@student_required
def student_results(request):
    """
    Student examination results view.
    Only displays marks that are in 'PUBLISHED' status.
    """
    from academics.models import StudentMark

    student = getattr(request.user, 'student_profile', None)
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    # Filter published marks only
    published_marks = StudentMark.objects.filter(
        student=student,
        status='PUBLISHED'
    ).select_related(
        'examination__unit__course',
        'examination__period'
    ).order_by(
        '-examination__period__academic_year',
        'examination__period__semester',
        'examination__unit__code'
    )

    # Compute summary statistics
    total_units_passed = sum(1 for m in published_marks if m.grade in ['A', 'B', 'C', 'D'])
    total_units_failed = sum(1 for m in published_marks if m.grade in ['E', 'F'])
    average_score = round(sum(float(m.total_mark or 0) for m in published_marks) / len(published_marks), 2) if published_marks else 0.0

    context = {
        'student': student,
        'published_marks': published_marks,
        'total_published': published_marks.count(),
        'total_units_passed': total_units_passed,
        'total_units_failed': total_units_failed,
        'average_score': average_score,
        'today': date.today(),
    }
    return render(request, 'student/results.html', context)
