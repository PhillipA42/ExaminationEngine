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
    """Printable Examination Pass / Exam Card for the logged-in student."""
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

    # Verification Reference Code
    verification_code = f"PASS-2026-{student.registration_number.replace('/', '-')}"

    context = {
        'student': student,
        'allocations': allocations,
        'active_period': active_period,
        'verification_code': verification_code,
        'generation_date': date.today(),
    }
    return render(request, 'student/exam_pass.html', context)
