import csv
from datetime import date
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db.models import Count, Q

from academics.models import (
    Student, Lecturer, Department, School, Unit, UnitRegistration,
    StudentMark, ResultSubmission, ResultWorkflowAudit, ReconciliationReport
)
from academics.reconciliation import ExamReconciliationEngine
from academics.results_services import BulkMarkUploadService, ResultWorkflowService
from scheduling.models import Examination, ExaminationPeriod
from authentication.models import Role


# -------------------------------------------------------------
# Role-based Decorators
# -------------------------------------------------------------
def lecturer_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/invigilator/login/?next={request.path}")
        if not hasattr(request.user, 'lecturer_profile') and not request.user.is_superuser:
            return HttpResponseForbidden("Access restricted: Lecturer profile required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def cod_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/invigilator/login/?next={request.path}")
        roles = set(request.user.user_roles.values_list('role__name', flat=True)) if request.user.is_authenticated else set()
        if not (request.user.is_superuser or Role.COD in roles or Role.ADMIN in roles):
            return HttpResponseForbidden("Access restricted: Chairman of Department (COD) authorization required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def dean_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/invigilator/login/?next={request.path}")
        roles = set(request.user.user_roles.values_list('role__name', flat=True)) if request.user.is_authenticated else set()
        if not (request.user.is_superuser or Role.DEAN in roles or Role.ADMIN in roles):
            return HttpResponseForbidden("Access restricted: Dean of School authorization required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def exam_officer_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/invigilator/login/?next={request.path}")
        roles = set(request.user.user_roles.values_list('role__name', flat=True)) if request.user.is_authenticated else set()
        if not (request.user.is_superuser or Role.EXAM_OFFICER in roles or Role.ADMIN in roles):
            return HttpResponseForbidden("Access restricted: Examination Officer authorization required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# -------------------------------------------------------------
# 1. Lecturer Results Views
# -------------------------------------------------------------
@lecturer_required
def lecturer_results_dashboard(request):
    """
    Lecturer dashboard listing assigned examinations and mark submission statuses.
    """
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if not lecturer and request.user.is_superuser:
        lecturer = Lecturer.objects.first()

    # Find examinations for lecturer's department or assigned duties
    examinations = Examination.objects.filter(
        unit__course__department=lecturer.department
    ).select_related('unit', 'period').prefetch_related('result_submissions')

    exam_cards = []
    for exam in examinations:
        submission = ResultSubmission.objects.filter(examination=exam, lecturer=lecturer).first()
        registered_count = UnitRegistration.objects.filter(
            unit=exam.unit,
            academic_year=exam.period.academic_year,
            semester=exam.period.semester,
            registration_status='REGISTERED'
        ).count()
        marks_count = StudentMark.objects.filter(examination=exam).count()

        exam_cards.append({
            'examination': exam,
            'submission': submission,
            'registered_count': registered_count,
            'marks_count': marks_count,
            'status': submission.status if submission else 'NOT_STARTED',
            'status_display': submission.get_status_display() if submission else 'Not Started',
        })

    return render(request, 'results/lecturer_dashboard.html', {
        'lecturer': lecturer,
        'exam_cards': exam_cards,
    })


@lecturer_required
def lecturer_mark_entry(request, examination_id):
    """
    Lecturer manual mark entry grid with real-time total/grade calculation.
    """
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if not lecturer and request.user.is_superuser:
        lecturer = Lecturer.objects.first()

    examination = get_object_or_404(
        Examination.objects.select_related('unit__course__department__school', 'period'),
        id=examination_id
    )

    submission = ResultWorkflowService.get_or_create_submission(examination, lecturer)

    # Fetch registered students for this unit
    registrations = UnitRegistration.objects.filter(
        unit=examination.unit,
        academic_year=examination.period.academic_year,
        semester=examination.period.semester,
        registration_status='REGISTERED'
    ).select_related('student__user').order_by('student__registration_number')

    # Fetch existing marks map
    existing_marks = {
        m.student_id: m for m in StudentMark.objects.filter(examination=examination)
    }

    students_roster = []
    for reg in registrations:
        std = reg.student
        mark = existing_marks.get(std.id)
        students_roster.append({
            'student': std,
            'coursework_mark': mark.coursework_mark if mark else '',
            'exam_mark': mark.exam_mark if mark else '',
            'total_mark': mark.total_mark if mark else '',
            'grade': mark.grade if mark else '',
            'status': mark.status if mark else 'DRAFT',
        })

    # Fetch latest reconciliation report if any
    latest_report = ReconciliationReport.objects.filter(examination=examination).order_by('-generated_at').first()

    can_edit = submission.status in ['DRAFT', 'COD_REJECTED', 'DEAN_REJECTED', 'FINAL_REJECTED']

    return render(request, 'results/mark_entry.html', {
        'examination': examination,
        'submission': submission,
        'students_roster': students_roster,
        'latest_report': latest_report,
        'can_edit': can_edit,
    })


@lecturer_required
def lecturer_bulk_upload(request, examination_id):
    """
    Bulk mark upload page with CSV / Excel upload, format instructions, and atomic validation feedback.
    """
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if not lecturer and request.user.is_superuser:
        lecturer = Lecturer.objects.first()

    examination = get_object_or_404(
        Examination.objects.select_related('unit', 'period'),
        id=examination_id
    )
    submission = ResultWorkflowService.get_or_create_submission(examination, lecturer)

    if request.method == 'POST' and request.FILES.get('mark_file'):
        file_obj = request.FILES['mark_file']
        result = BulkMarkUploadService.process_file(file_obj, submission, request.user)

        if result['success']:
            messages.success(request, f"Successfully uploaded and validated {result['processed_count']} student marks!")
            return redirect('lecturer_mark_entry', examination_id=examination.id)
        else:
            return render(request, 'results/bulk_upload.html', {
                'examination': examination,
                'submission': submission,
                'upload_errors': result['errors'],
                'preview': result.get('preview', []),
            })

    return render(request, 'results/bulk_upload.html', {
        'examination': examination,
        'submission': submission,
    })


@lecturer_required
def download_sample_csv(request, examination_id):
    """Generates and downloads a pre-populated CSV template with registered students."""
    examination = get_object_or_404(Examination.objects.select_related('unit', 'period'), id=examination_id)
    registrations = UnitRegistration.objects.filter(
        unit=examination.unit,
        academic_year=examination.period.academic_year,
        semester=examination.period.semester,
        registration_status='REGISTERED'
    ).select_related('student__user').order_by('student__registration_number')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="Marks_Template_{examination.unit.code}.csv"'

    writer = csv.writer(response)
    writer.writerow(['registration_number', 'student_name', 'cat_mark', 'exam_mark'])
    for reg in registrations:
        writer.writerow([reg.student.registration_number, reg.student.user.get_full_name(), '30', '60'])

    return response


# -------------------------------------------------------------
# 2. COD Results Review Views
# -------------------------------------------------------------
@cod_required
def cod_results_dashboard(request):
    """
    Departmental Chairman (COD) dashboard showing submissions across their department.
    """
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if lecturer:
        department = lecturer.department
        submissions = ResultSubmission.objects.filter(department=department).select_related(
            'unit', 'examination__period', 'lecturer__user'
        )
    else:
        department = Department.objects.first()
        submissions = ResultSubmission.objects.all().select_related(
            'unit', 'examination__period', 'lecturer__user'
        )

    pending_count = submissions.filter(status='SUBMITTED').count()
    approved_count = submissions.filter(status__in=['COD_APPROVED', 'DEAN_APPROVED', 'PUBLISHED']).count()
    rejected_count = submissions.filter(status__in=['COD_REJECTED', 'DEAN_REJECTED', 'FINAL_REJECTED']).count()
    published_count = submissions.filter(status='PUBLISHED').count()

    return render(request, 'results/cod_dashboard.html', {
        'department': department,
        'submissions': submissions,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
        'published_count': published_count,
    })


@cod_required
def cod_review_submission(request, submission_id):
    """
    COD detailed inspection view of a submission: marks, 3-way reconciliation audit, and approve/reject modal.
    """
    submission = get_object_or_404(
        ResultSubmission.objects.select_related(
            'unit', 'examination__period', 'lecturer__user', 'department', 'school'
        ),
        id=submission_id
    )

    marks = StudentMark.objects.filter(examination=submission.examination).select_related('student__user')
    latest_report = ReconciliationReport.objects.filter(examination=submission.examination).order_by('-generated_at').first()
    anomalies = latest_report.anomalies.all() if latest_report else []
    audit_trail = submission.audit_trail.all()

    if request.method == 'POST':
        action_type = request.POST.get('action', '').upper()
        comments = request.POST.get('comments', '')
        rejection_reason = request.POST.get('rejection_reason', '')

        try:
            ResultWorkflowService.cod_review(
                submission, request.user, action=action_type, comments=comments, rejection_reason=rejection_reason
            )
            if action_type == 'APPROVE':
                messages.success(request, f"Results for {submission.unit.code} approved and forwarded to Dean of School.")
            else:
                messages.warning(request, f"Results for {submission.unit.code} rejected and returned to Lecturer.")
            return redirect('cod_results_dashboard')
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return render(request, 'results/cod_review_detail.html', {
        'submission': submission,
        'marks': marks,
        'latest_report': latest_report,
        'anomalies': anomalies,
        'audit_trail': audit_trail,
    })


# -------------------------------------------------------------
# 3. Dean Results Review Views
# -------------------------------------------------------------
@dean_required
def dean_results_dashboard(request):
    """
    Dean of School dashboard showing submissions across all departments in the School.
    """
    lecturer = getattr(request.user, 'lecturer_profile', None)
    if lecturer:
        school = lecturer.department.school
        submissions = ResultSubmission.objects.filter(school=school).select_related(
            'unit', 'examination__period', 'lecturer__user', 'department'
        )
    else:
        school = School.objects.first()
        submissions = ResultSubmission.objects.all().select_related(
            'unit', 'examination__period', 'lecturer__user', 'department'
        )

    pending_count = submissions.filter(status='COD_APPROVED').count()
    approved_count = submissions.filter(status__in=['DEAN_APPROVED', 'PUBLISHED']).count()
    rejected_count = submissions.filter(status__in=['DEAN_REJECTED', 'FINAL_REJECTED']).count()
    published_count = submissions.filter(status='PUBLISHED').count()

    return render(request, 'results/dean_dashboard.html', {
        'school': school,
        'submissions': submissions,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
        'published_count': published_count,
    })


@dean_required
def dean_review_submission(request, submission_id):
    """
    Dean detailed review of a submission forwarded by COD.
    """
    submission = get_object_or_404(
        ResultSubmission.objects.select_related(
            'unit', 'examination__period', 'lecturer__user', 'department', 'school'
        ),
        id=submission_id
    )

    marks = StudentMark.objects.filter(examination=submission.examination).select_related('student__user')
    latest_report = ReconciliationReport.objects.filter(examination=submission.examination).order_by('-generated_at').first()
    anomalies = latest_report.anomalies.all() if latest_report else []
    audit_trail = submission.audit_trail.all()

    if request.method == 'POST':
        action_type = request.POST.get('action', '').upper()
        comments = request.POST.get('comments', '')
        rejection_reason = request.POST.get('rejection_reason', '')

        try:
            ResultWorkflowService.dean_review(
                submission, request.user, action=action_type, comments=comments, rejection_reason=rejection_reason
            )
            if action_type == 'APPROVE':
                messages.success(request, f"Results for {submission.unit.code} approved and forwarded to Examination Authority.")
            else:
                messages.warning(request, f"Results for {submission.unit.code} rejected and returned.")
            return redirect('dean_results_dashboard')
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return render(request, 'results/dean_review_detail.html', {
        'submission': submission,
        'marks': marks,
        'latest_report': latest_report,
        'anomalies': anomalies,
        'audit_trail': audit_trail,
    })


# -------------------------------------------------------------
# 4. Examination Officer & Final Authority Views
# -------------------------------------------------------------
@exam_officer_required
def officer_results_dashboard(request):
    """
    Institution-wide Examination Officer dashboard for final result publication and verification.
    """
    submissions = ResultSubmission.objects.all().select_related(
        'unit', 'examination__period', 'lecturer__user', 'department', 'school'
    )

    pending_final_count = submissions.filter(status='DEAN_APPROVED').count()
    published_count = submissions.filter(status='PUBLISHED').count()
    in_workflow_count = submissions.filter(status__in=['SUBMITTED', 'COD_APPROVED']).count()
    rejected_count = submissions.filter(status__in=['COD_REJECTED', 'DEAN_REJECTED', 'FINAL_REJECTED']).count()

    return render(request, 'results/officer_dashboard.html', {
        'submissions': submissions,
        'pending_final_count': pending_final_count,
        'published_count': published_count,
        'in_workflow_count': in_workflow_count,
        'rejected_count': rejected_count,
    })


@exam_officer_required
def officer_review_submission(request, submission_id):
    """
    Examination Officer review and publication detail.
    """
    submission = get_object_or_404(
        ResultSubmission.objects.select_related(
            'unit', 'examination__period', 'lecturer__user', 'department', 'school'
        ),
        id=submission_id
    )

    marks = StudentMark.objects.filter(examination=submission.examination).select_related('student__user')
    latest_report = ReconciliationReport.objects.filter(examination=submission.examination).order_by('-generated_at').first()
    anomalies = latest_report.anomalies.all() if latest_report else []
    audit_trail = submission.audit_trail.all()

    if request.method == 'POST':
        action_type = request.POST.get('action', '').upper()
        comments = request.POST.get('comments', '')
        rejection_reason = request.POST.get('rejection_reason', '')

        try:
            ResultWorkflowService.final_review_and_publish(
                submission, request.user, action=action_type, comments=comments, rejection_reason=rejection_reason
            )
            if action_type in ['APPROVE', 'PUBLISH']:
                messages.success(request, f"Official Results for {submission.unit.code} have been PUBLISHED to the Student Portal!")
            else:
                messages.warning(request, f"Results for {submission.unit.code} rejected and returned for correction.")
            return redirect('officer_results_dashboard')
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return render(request, 'results/officer_review_detail.html', {
        'submission': submission,
        'marks': marks,
        'latest_report': latest_report,
        'anomalies': anomalies,
        'audit_trail': audit_trail,
    })
