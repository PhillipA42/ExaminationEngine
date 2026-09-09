from datetime import date
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.http import HttpResponseForbidden

from academics.models import Student
from scheduling.models import StudentExamAllocation, ExaminationPeriod


def student_required(view_func):
    """Decorator to ensure the authenticated user has an active Student profile."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/student/login/?next={request.path}")
        if not hasattr(request.user, 'student_profile') and not request.user.is_superuser:
            return HttpResponseForbidden("Access restricted: You must have a registered Student profile.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def student_login(request):
    """Student portal login view."""
    if request.user.is_authenticated and hasattr(request.user, 'student_profile'):
        return redirect('student_timetable')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not hasattr(user, 'student_profile') and not user.is_superuser:
                messages.error(request, "Account found, but no Student profile is associated with this username.")
            else:
                login(request, user)
                messages.success(request, f"Welcome, {user.get_full_name() or user.username}!")
                next_url = request.POST.get('next') or request.GET.get('next') or 'student_timetable'
                return redirect(next_url)
        else:
            messages.error(request, "Invalid registration/username or password. Please try again.")

    return render(request, 'student/login.html')


def student_logout(request):
    """Student portal logout view."""
    logout(request)
    messages.info(request, "You have been logged out safely.")
    return redirect('student_login')


@student_required
def student_timetable(request):
    """Personalized timetable dashboard for the logged-in student."""
    student = getattr(request.user, 'student_profile', None)
    
    # If superuser without student profile, pick first student for testing
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    today = date.today()

    # Fetch all exam allocations for this student with schedule & room details
    allocations_qs = StudentExamAllocation.objects.filter(
        student=student
    ).select_related(
        'examination__unit__course',
        'examination__period',
        'room__building__campus',
        'room__floor'
    ).order_by('examination__schedule__exam_date', 'examination__schedule__start_time')

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
def student_exam_pass(request):
    """Printable Examination Pass / Exam Card for the logged-in student.
    Dynamically embeds the student's eligibility status from EligibilityService."""
    from academics.eligibility_services import EligibilityService

    student = getattr(request.user, 'student_profile', None)
    if not student and request.user.is_superuser:
        student = Student.objects.first()

    # Fetch all exam allocations for the active semester
    allocations = StudentExamAllocation.objects.filter(
        student=student
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

    # Verification Reference Code
    verification_code = f"PASS-2026-{student.registration_number.replace('/', '-')}"

    context = {
        'student': student,
        'allocations': allocations,
        'active_period': active_period,
        'verification_code': verification_code,
        'generation_date': date.today(),
        'eligibility': eligibility_result,
    }
    return render(request, 'student/exam_pass.html', context)


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

