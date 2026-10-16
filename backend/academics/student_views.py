from datetime import date, datetime
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.contrib.auth import logout
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.core import signing
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.core.paginator import Paginator
from urllib.parse import urlencode

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
    now = timezone.localtime()
    next_exam = None
    upcoming = []

    for alloc in allocations:
        schedule = getattr(alloc.examination, 'schedule', None)
        if not schedule or not schedule.exam_date:
            continue
        item = {'allocation': alloc, 'exam': alloc.examination, 'schedule': schedule, 'room': alloc.room}
        exam_end = timezone.make_aware(
            datetime.combine(schedule.exam_date, schedule.end_time),
            timezone.get_current_timezone(),
        )
        if exam_end >= now:
            upcoming.append(item)
            if next_exam is None:
                next_exam = item

    return {
        'allocations': allocations,
        'next_exam': next_exam,
        'upcoming': upcoming[:5],
        'all_upcoming': upcoming,
        'upcoming_total': len(upcoming),
        'total': len(allocations),
        'today': today,
    }


def student_required(view_func):
    """Decorator to ensure the authenticated user has an active Student profile."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/student/login/?next={request.path}")
        student = getattr(request.user, 'student_profile', None)
        if student is None:
            if hasattr(request.user, 'lecturer_profile'):
                messages.info(request, "This account is registered for the Lecturer Portal. Redirected to your lecturer dashboard.")
                return redirect('invigilator_dashboard')
            return HttpResponseForbidden("A registered student profile is required to access the Student Portal.")
        if student.status != 'ACTIVE':
            return HttpResponseForbidden("An active student profile is required to access the Student Portal.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def student_portal_context(request):
    """Shared student-shell data, scoped to the authenticated account."""
    if not request.user.is_authenticated or not hasattr(request.user, 'student_profile'):
        return {}
    from academics.models import Notification
    return {
        'student_unread_notifications': Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count(),
    }


def student_login(request):
    """Backward-compatible redirect to the single credential-based login page."""
    next_url = request.GET.get('next') or request.POST.get('next')
    target = f"/login/?next={next_url}" if next_url else '/login/'
    return redirect(target)


@require_POST
def student_logout(request):
    """Student portal logout view."""
    logout(request)
    messages.info(request, "You have been logged out safely.")
    return redirect('student_login')


@student_required
def student_dashboard(request):
    """Personalized student landing page with the most relevant examination information first."""
    student = getattr(request.user, 'student_profile', None)

    summary = get_next_exam_summary(student)
    allocations = summary['allocations']
    next_exam = summary['next_exam']
    upcoming = summary['upcoming']

    active_period = next_exam['exam'].period if next_exam else None
    eligibility_status = 'NOT_APPLICABLE'
    eligibility_detail = 'Eligibility will be shown when an upcoming examination is published.'
    if next_exam:
        try:
            from academics.eligibility_services import EligibilityService
            evaluation = EligibilityService().evaluate_for_examination(student, next_exam['exam'])
            eligibility_status = evaluation.overall_status
            eligibility_detail = evaluation.evaluation_details.get('reason') or 'Eligibility status is based on the latest available examination records.'
        except Exception:
            eligibility_status = 'UNAVAILABLE'
            eligibility_detail = 'Eligibility verification is temporarily unavailable. Please try again later.'

    from academics.models import Notification
    recent_updates = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:4]
    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    eligible_upcoming_count = 0
    eligibility_summary_available = True
    for item in summary['all_upcoming']:
        try:
            from academics.eligibility_services import EligibilityService
            result = EligibilityService().evaluate_for_examination(student, item['exam'])
            eligible_upcoming_count += int(result.is_eligible)
        except Exception:
            eligibility_summary_available = False
            break
    if not eligibility_summary_available:
        eligible_upcoming_count = None

    context = {
        'student': student,
        'next_exam': next_exam,
        'upcoming_exams': upcoming,
        'upcoming_count': summary['upcoming_total'],
        'total_exams': len(allocations),
        'eligible_upcoming_count': eligible_upcoming_count,
        'eligibility_status': eligibility_status,
        'eligibility_detail': eligibility_detail,
        'active_period': active_period,
        'today': summary['today'],
        'recent_updates': recent_updates,
        'unread_count': unread_count,
    }
    return render(request, 'student/dashboard.html', context)


@student_required
def student_profile(request):
    """Read-only student profile from the application's linked academic record."""
    student = getattr(request.user, 'student_profile', None)
    context = {
        'student': student,
        'academic_status': getattr(student, 'status', 'ACTIVE'),
    }
    return render(request, 'student/profile.html', context)


@student_required
def student_timetable(request):
    """Personalized timetable dashboard for the logged-in student."""
    student = getattr(request.user, 'student_profile', None)
    
    today = timezone.localdate()

    # Fetch all exam allocations for this student with schedule & room details
    allocations_qs = get_student_allocations(student)
    period_id = request.GET.get('period', '').strip()
    period_options = ExaminationPeriod.objects.filter(
        examinations__student_allocations__student=student,
        status='PUBLISHED',
    ).distinct().order_by('-academic_year', '-semester')
    if period_id.isdigit():
        allocations_qs = allocations_qs.filter(examination__period_id=int(period_id))
        if not period_options.filter(pk=int(period_id)).exists():
            period_id = ''
            allocations_qs = get_student_allocations(student)

    examination_status = request.GET.get('status', '').strip().lower()
    if examination_status == 'upcoming':
        allocations_qs = allocations_qs.filter(
            Q(examination__schedule__exam_date__gt=today)
            | Q(examination__schedule__exam_date=today, examination__schedule__end_time__gte=timezone.localtime().time())
        )
    elif examination_status == 'past':
        allocations_qs = allocations_qs.filter(
            Q(examination__schedule__exam_date__lt=today)
            | Q(examination__schedule__exam_date=today, examination__schedule__end_time__lt=timezone.localtime().time())
        )

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
            exam_end = timezone.make_aware(
                datetime.combine(schedule.exam_date, schedule.end_time),
                timezone.get_current_timezone(),
            )
            if exam_end < timezone.localtime():
                is_past = True
                completed_count += 1
            elif schedule.exam_date == today:
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
    active_period = timetable_items[0]['exam'].period if timetable_items else None

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
        'period_options': period_options,
        'selected_period': period_id,
        'selected_status': examination_status,
    }
    return render(request, 'student/timetable.html', context)


@student_required
def student_exam_detail(request, allocation_id):
    """Central detail page for a single examination allocation."""
    student = getattr(request.user, 'student_profile', None)
    allocation = get_object_or_404(
        StudentExamAllocation, id=allocation_id, student=student,
        examination__period__status='PUBLISHED',
    )
    exam = allocation.examination
    schedule = getattr(exam, 'schedule', None)
    try:
        from academics.eligibility_services import EligibilityService
        eligibility = EligibilityService().evaluate_for_examination(student, exam)
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
    allocation = get_object_or_404(
        StudentExamAllocation, id=allocation_id, student=student,
        examination__period__status='PUBLISHED',
    )
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
    """Printable summary of the logged-in student's published examination allocations."""

    student = getattr(request.user, 'student_profile', None)
    # Fetch all exam allocations for the active semester
    allocations = StudentExamAllocation.objects.filter(
        student=student,
        examination__period__status='PUBLISHED',
        examination__schedule__isnull=False,
    ).select_related(
        'examination__unit__course',
        'examination__period',
        'room__building__campus',
        'room__floor'
    ).order_by('examination__schedule__exam_date', 'examination__schedule__start_time')
    allocations = allocations.filter(examination__schedule__isnull=False)

    period_options = ExaminationPeriod.objects.filter(
        examinations__student_allocations__student=student,
        status='PUBLISHED',
    ).distinct().order_by('-academic_year', '-semester')
    requested_period = request.GET.get('period', '').strip()
    if requested_period.isdigit() and period_options.filter(pk=int(requested_period)).exists():
        active_period = period_options.get(pk=int(requested_period))
        allocations = allocations.filter(examination__period=active_period)
    else:
        active_period = period_options.first()
        if active_period:
            allocations = allocations.filter(examination__period=active_period)
        requested_period = str(active_period.pk) if active_period else ''

    # Signed token deliberately contains no personal data.  Verification always
    # re-queries authoritative allocations, so timetable changes invalidate it.
    pass_token = signing.dumps({
        'student_id': student.id,
        'period_id': active_period.id if active_period else None,
        'allocation_ids': list(allocations.values_list('id', flat=True)),
    }, salt='exam-pass')
    verification_code = None

    context = {
        'student': student,
        'allocations': allocations,
        'active_period': active_period,
        'period_options': period_options,
        'selected_period': requested_period,
        'verification_code': verification_code,
        'pass_token': pass_token,
        'generation_date': date.today(),
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

    notifications = Notification.objects.filter(recipient=request.user).prefetch_related('deliveries')
    read_filter = request.GET.get('read', '').strip().lower()
    if read_filter == 'unread':
        notifications = notifications.filter(is_read=False)
    elif read_filter == 'read':
        notifications = notifications.filter(is_read=True)
    type_filter = request.GET.get('type', '').strip()
    valid_types = {choice[0] for choice in Notification.NOTIFICATION_TYPES}
    if type_filter in valid_types:
        notifications = notifications.filter(notification_type=type_filter)
    notifications = notifications.order_by('-created_at')[:50]

    svc = NotificationService()
    unread_count = svc.get_unread_count(request.user)

    context = {
        'notifications': notifications,
        'unread_count': unread_count,
        'selected_read_filter': read_filter,
        'selected_type_filter': type_filter if type_filter in valid_types else '',
        'notification_types': Notification.NOTIFICATION_TYPES,
    }
    return render(request, 'student/notifications.html', context)


@student_required
@require_POST
def student_mark_notification_read(request, notification_id):
    """Mark only the authenticated student's notification as read."""
    from academics.notification_services import NotificationService
    if NotificationService().mark_read(notification_id, request.user):
        messages.success(request, 'Notification marked as read.')
    return redirect('student_notifications')


@student_required
@require_POST
def student_mark_all_notifications_read(request):
    """Mark all notifications belonging to the authenticated student as read."""
    from academics.notification_services import NotificationService
    NotificationService().mark_all_read(request.user)
    messages.success(request, 'All notifications have been marked as read.')
    return redirect('student_notifications')


@student_required
def student_results(request):
    """
    Student examination results view.
    Only displays marks that are in 'PUBLISHED' status.
    """
    from academics.models import StudentMark

    student = getattr(request.user, 'student_profile', None)
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

    period_id = request.GET.get('period', '').strip()
    period_options = ExaminationPeriod.objects.filter(
        examinations__student_marks__student=student,
        examinations__student_marks__status='PUBLISHED',
    ).distinct().order_by('-academic_year', '-semester')
    if period_id.isdigit() and period_options.filter(pk=int(period_id)).exists():
        published_marks = published_marks.filter(examination__period_id=int(period_id))
    else:
        period_id = ''

    # Compute summary statistics
    total_units_passed = sum(1 for m in published_marks if m.grade in ['A', 'B', 'C', 'D'])
    total_units_failed = sum(1 for m in published_marks if m.grade in ['E', 'F'])
    average_score = round(sum(float(m.total_mark or 0) for m in published_marks) / len(published_marks), 2) if published_marks else None

    context = {
        'student': student,
        'published_marks': published_marks,
        'total_published': published_marks.count(),
        'total_units_passed': total_units_passed,
        'total_units_failed': total_units_failed,
        'average_score': average_score,
        'today': date.today(),
        'period_options': period_options,
        'selected_period': period_id,
    }
    return render(request, 'student/results.html', context)


@student_required
@require_GET
def student_attendance(request):
    """Read-only attendance status for this student's published examination allocations."""
    from invigilators.models import ExamAttendance

    student = request.user.student_profile
    allocations = get_student_allocations(student)
    attendance_by_exam = {
        record.examination_id: record
        for record in ExamAttendance.objects.filter(
            student=student,
            examination_id__in=allocations.values('examination_id'),
        ).select_related('examination')
    }
    entries = [
        {
            'allocation': allocation,
            'attendance': attendance_by_exam.get(allocation.examination_id),
            'schedule': getattr(allocation.examination, 'schedule', None),
        }
        for allocation in allocations
    ]
    return render(request, 'student/attendance.html', {'student': student, 'attendance_entries': entries})


@student_required
@require_GET
def student_eligibility(request):
    """Display eligibility evaluations without allowing student changes."""
    from academics.eligibility_services import EligibilityService

    student = request.user.student_profile
    evaluations = []
    for allocation in get_student_allocations(student):
        try:
            result = EligibilityService().evaluate_for_examination(student, allocation.examination)
            status = result.overall_status
            detail = result.evaluation_details.get('reason') or 'Based on the latest available examination records.'
        except Exception:
            status = 'UNAVAILABLE'
            detail = 'Eligibility verification is temporarily unavailable. Please try again later.'
        evaluations.append({'allocation': allocation, 'status': status, 'detail': detail})
    return render(request, 'student/eligibility.html', {'student': student, 'evaluations': evaluations})


@student_required
@require_GET
def student_history(request):
    """Paginated history of the authenticated student's past allocations."""
    student = request.user.student_profile
    today = timezone.localdate()
    local_time = timezone.localtime().time()
    past_exams = Q(examinations__schedule__exam_date__lt=today) | Q(
        examinations__schedule__exam_date=today,
        examinations__schedule__end_time__lt=local_time,
    )
    past_allocations = get_student_allocations(student).filter(
        Q(examination__schedule__exam_date__lt=today)
        | Q(examination__schedule__exam_date=today, examination__schedule__end_time__lt=local_time)
    )
    period_id = request.GET.get('period', '').strip()
    period_options = ExaminationPeriod.objects.filter(
        examinations__student_allocations__student=student,
    ).filter(past_exams).distinct().order_by('-academic_year', '-semester')
    if period_id.isdigit() and period_options.filter(pk=int(period_id)).exists():
        past_allocations = past_allocations.filter(examination__period_id=int(period_id))
    else:
        period_id = ''
    page_obj = Paginator(past_allocations, 20).get_page(request.GET.get('page'))
    from academics.models import StudentMark
    from invigilators.models import ExamAttendance
    page_exam_ids = [item.examination_id for item in page_obj.object_list]
    attendance_by_exam = {
        item.examination_id: item
        for item in ExamAttendance.objects.filter(student=student, examination_id__in=page_exam_ids)
    }
    published_mark_ids = set(StudentMark.objects.filter(
        student=student, examination_id__in=page_exam_ids, status='PUBLISHED'
    ).values_list('examination_id', flat=True))
    history_entries = [
        {
            'allocation': allocation,
            'attendance': attendance_by_exam.get(allocation.examination_id),
            'has_published_result': allocation.examination_id in published_mark_ids,
        }
        for allocation in page_obj.object_list
    ]
    return render(request, 'student/history.html', {
        'student': student,
        'page_obj': page_obj,
        'history_entries': history_entries,
        'period_options': period_options,
        'selected_period': period_id,
    })


@student_required
@require_GET
def student_help(request):
    """Provide examination guidance without inventing university contact information."""
    return render(request, 'student/help.html', {'student': request.user.student_profile})


@student_required
@require_GET
def student_navigation(request):
    """Let students choose one of their own upcoming allocations for directions."""
    student = request.user.student_profile
    today = timezone.localdate()
    allocations = get_student_allocations(student).filter(
        Q(examination__schedule__exam_date__gt=today)
        | Q(examination__schedule__exam_date=today, examination__schedule__end_time__gte=timezone.localtime().time())
    )
    return render(request, 'student/navigation.html', {'student': student, 'allocations': allocations})
