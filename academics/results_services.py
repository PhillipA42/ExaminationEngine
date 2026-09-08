import csv
import io
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from academics.models import (
    Student, UnitRegistration, StudentMark, ResultSubmission, ResultWorkflowAudit
)
from academics.reconciliation import ExamReconciliationEngine
from authentication.models import Role


class BulkMarkUploadService:
    """
    Atomic bulk mark upload service supporting CSV and XLSX files.
    Validates student enrollment, mark boundaries, duplication, and enforces complete transactional rollback on any error.
    """

    MAX_COURSEWORK_MARK = Decimal('40.00')
    MAX_EXAM_MARK = Decimal('70.00')
    MAX_TOTAL_MARK = Decimal('100.00')

    @classmethod
    def process_file(cls, file_obj, submission, actor):
        """
        Parses and validates an uploaded mark file (CSV or XLSX).
        Returns a dict: {'success': bool, 'errors': list, 'processed_count': int, 'preview': list}
        """
        filename = file_obj.name.lower()
        if filename.endswith('.csv'):
            rows = cls._parse_csv(file_obj)
        elif filename.endswith(('.xlsx', '.xls')):
            rows = cls._parse_excel(file_obj)
        else:
            return {
                'success': False,
                'errors': ['Unsupported file format. Please upload a standard .csv or .xlsx file.'],
                'processed_count': 0,
                'preview': []
            }

        if not rows:
            return {
                'success': False,
                'errors': ['The uploaded file is empty or contains no data rows.'],
                'processed_count': 0,
                'preview': []
            }

        # Validate rows atomically
        validation_result = cls._validate_and_build_records(rows, submission)
        if not validation_result['is_valid']:
            return {
                'success': False,
                'errors': validation_result['errors'],
                'processed_count': 0,
                'preview': validation_result['parsed_records'][:10]
            }

        # Commit atomically
        parsed_records = validation_result['parsed_records']
        try:
            with transaction.atomic():
                saved_marks = []
                for item in parsed_records:
                    student = item['student']
                    coursework = item['coursework_mark']
                    exam_mark = item['exam_mark']

                    mark_obj, created = StudentMark.objects.update_or_create(
                        student=student,
                        examination=submission.examination,
                        defaults={
                            'coursework_mark': coursework,
                            'exam_mark': exam_mark,
                            'submitted_by': submission.lecturer,
                            'submission': submission,
                            'status': 'DRAFT' if submission.status in ['DRAFT', 'COD_REJECTED', 'DEAN_REJECTED', 'FINAL_REJECTED'] else 'SUBMITTED'
                        }
                    )
                    saved_marks.append(mark_obj)

                # Record workflow audit
                ResultWorkflowAudit.objects.create(
                    submission=submission,
                    actor=actor,
                    action='BULK_UPLOAD',
                    previous_status=submission.status,
                    new_status=submission.status,
                    comment=f"Bulk imported {len(saved_marks)} student marks from '{file_obj.name}'."
                )

            return {
                'success': True,
                'errors': [],
                'processed_count': len(saved_marks),
                'preview': parsed_records[:10]
            }
        except Exception as e:
            return {
                'success': False,
                'errors': [f"Database transaction error during mark import: {str(e)}"],
                'processed_count': 0,
                'preview': []
            }

    @classmethod
    def _parse_csv(cls, file_obj):
        file_obj.seek(0)
        content = file_obj.read()
        if isinstance(content, bytes):
            # Try utf-8-sig (handles Excel BOM) then utf-8, then latin1
            try:
                decoded = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                decoded = content.decode('latin-1')
        else:
            decoded = content

        reader = csv.reader(io.StringIO(decoded))
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        return rows

    @classmethod
    def _parse_excel(cls, file_obj):
        import openpyxl
        file_obj.seek(0)
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        sheet = wb.active
        rows = []
        for row in sheet.iter_rows(values_only=True):
            if any(cell is not None and str(cell).strip() for cell in row):
                row_str = [str(cell).strip() if cell is not None else '' for cell in row]
                rows.append(row_str)
        return rows

    @classmethod
    def _validate_and_build_records(cls, rows, submission):
        errors = []
        parsed_records = []
        seen_reg_numbers = set()

        if len(rows) < 2:
            return {
                'is_valid': False,
                'errors': ['File must contain a header row and at least one data row.'],
                'parsed_records': []
            }

        # Normalize header
        header = [cls._clean_header(col) for col in rows[0]]
        reg_idx = cls._find_column_index(header, ['registration_number', 'reg_no', 'reg_number', 'regno', 'student_id', 'adm_no'])
        cat_idx = cls._find_column_index(header, ['cat_mark', 'coursework_mark', 'coursework', 'cat', 'ca_mark'])
        exam_idx = cls._find_column_index(header, ['exam_mark', 'final_exam', 'exam', 'examination_mark', 'exam_score'])

        if reg_idx is None:
            errors.append("Missing required header: 'registration_number' or 'reg_no'.")
        if cat_idx is None:
            errors.append("Missing required header: 'cat_mark' or 'coursework_mark'.")
        if exam_idx is None:
            errors.append("Missing required header: 'exam_mark' or 'final_exam'.")

        if errors:
            return {'is_valid': False, 'errors': errors, 'parsed_records': []}

        unit = submission.unit
        academic_year = submission.academic_year
        semester = submission.semester

        # Pre-fetch registered students for fast validation
        registrations = UnitRegistration.objects.filter(
            unit=unit,
            academic_year=academic_year,
            semester=semester,
            registration_status='REGISTERED'
        ).select_related('student__user')
        registered_students_by_reg = {
            r.student.registration_number.upper().strip(): r.student for r in registrations
        }

        all_students_by_reg = {
            s.registration_number.upper().strip(): s
            for s in Student.objects.filter(
                registration_number__in=[r[reg_idx].upper().strip() for r in rows[1:] if len(r) > reg_idx and r[reg_idx].strip()]
            ).select_related('user')
        }

        for row_num, row in enumerate(rows[1:], start=2):
            if not any(cell.strip() for cell in row):
                continue

            if len(row) <= max(reg_idx, cat_idx, exam_idx):
                errors.append(f"Row {row_num}: Malformed row with insufficient columns.")
                continue

            raw_reg = row[reg_idx].strip()
            raw_cat = row[cat_idx].strip()
            raw_exam = row[exam_idx].strip()

            if not raw_reg:
                errors.append(f"Row {row_num}: Missing registration number.")
                continue

            reg_norm = raw_reg.upper()

            # Duplicate check within file
            if reg_norm in seen_reg_numbers:
                errors.append(f"Row {row_num}: Duplicate registration number '{raw_reg}' found in uploaded file.")
                continue
            seen_reg_numbers.add(reg_norm)

            # Check student exists
            student = all_students_by_reg.get(reg_norm)
            if not student:
                errors.append(f"Row {row_num}: Student '{raw_reg}' does not exist in the system.")
                continue

            # Check student registered for unit in this period
            if reg_norm not in registered_students_by_reg:
                errors.append(
                    f"Row {row_num}: Student '{raw_reg}' ({student.user.get_full_name()}) is not registered for {unit.code} "
                    f"in {academic_year} Semester {semester}."
                )
                continue

            # Parse numeric marks
            try:
                cat_val = Decimal(str(raw_cat))
                if cat_val < Decimal('0.00') or cat_val > cls.MAX_COURSEWORK_MARK:
                    errors.append(f"Row {row_num}: CAT mark ({raw_cat}) is out of range [0.00 - {cls.MAX_COURSEWORK_MARK}].")
                    continue
            except (InvalidOperation, ValueError):
                errors.append(f"Row {row_num}: CAT mark '{raw_cat}' is not a valid decimal number.")
                continue

            try:
                exam_val = Decimal(str(raw_exam))
                if exam_val < Decimal('0.00') or exam_val > cls.MAX_EXAM_MARK:
                    errors.append(f"Row {row_num}: Exam mark ({raw_exam}) is out of range [0.00 - {cls.MAX_EXAM_MARK}].")
                    continue
            except (InvalidOperation, ValueError):
                errors.append(f"Row {row_num}: Exam mark '{raw_exam}' is not a valid decimal number.")
                continue

            total_val = cat_val + exam_val
            if total_val > cls.MAX_TOTAL_MARK:
                errors.append(f"Row {row_num}: Combined total mark ({total_val}) exceeds maximum of {cls.MAX_TOTAL_MARK}.")
                continue

            # Calculate grade
            grade = cls.calculate_grade(total_val)

            parsed_records.append({
                'row_num': row_num,
                'student': student,
                'registration_number': raw_reg,
                'student_name': student.user.get_full_name(),
                'coursework_mark': cat_val,
                'exam_mark': exam_val,
                'total_mark': total_val,
                'grade': grade
            })

        return {
            'is_valid': len(errors) == 0,
            'errors': errors,
            'parsed_records': parsed_records
        }

    @staticmethod
    def _clean_header(col):
        if not col:
            return ''
        return str(col).strip().lower().replace(' ', '_').replace('-', '_').replace('.', '')

    @staticmethod
    def _find_column_index(header, aliases):
        for alias in aliases:
            if alias in header:
                return header.index(alias)
        return None

    @staticmethod
    def calculate_grade(total_mark):
        """Standardized grading calculation."""
        if total_mark >= 70:
            return 'A'
        elif total_mark >= 60:
            return 'B'
        elif total_mark >= 50:
            return 'C'
        elif total_mark >= 40:
            return 'D'
        else:
            return 'E'


class ResultWorkflowService:
    """
    State machine and orchestration service for examination result lifecycle:
    LECTURER (Draft / Submit) -> COD (Review / Approve / Reject) -> DEAN (Review / Approve / Reject) -> EXAM_OFFICER (Publish / Reject).
    """

    @classmethod
    def get_or_create_submission(cls, examination, lecturer):
        """
        Retrieves or initializes a ResultSubmission for an examination and assigned lecturer.
        """
        period = examination.period
        unit = examination.unit
        department = unit.course.department
        school = department.school

        submission, created = ResultSubmission.objects.get_or_create(
            examination=examination,
            lecturer=lecturer,
            defaults={
                'unit': unit,
                'academic_year': period.academic_year,
                'semester': period.semester,
                'department': department,
                'school': school,
                'status': 'DRAFT',
            }
        )
        if created:
            ResultWorkflowAudit.objects.create(
                submission=submission,
                actor=lecturer.user,
                action='CREATED',
                previous_status='',
                new_status='DRAFT',
                comment="Result submission workspace initialized."
            )
        return submission

    @classmethod
    def save_manual_marks(cls, submission, actor, marks_payload):
        """
        Saves or updates manual draft marks entered by the lecturer.
        marks_payload: list of dicts [{'student_id': 1, 'coursework_mark': 25, 'exam_mark': 50}]
        """
        if submission.status not in ['DRAFT', 'COD_REJECTED', 'DEAN_REJECTED', 'FINAL_REJECTED']:
            raise ValidationError(f"Marks cannot be edited while in '{submission.get_status_display()}' status.")

        unit = submission.unit
        period = submission.examination.period

        # Fetch valid registered students
        valid_student_ids = set(UnitRegistration.objects.filter(
            unit=unit,
            academic_year=period.academic_year,
            semester=period.semester,
            registration_status='REGISTERED'
        ).values_list('student_id', flat=True))

        saved_count = 0
        with transaction.atomic():
            for item in marks_payload:
                student_id = item.get('student_id')
                if student_id not in valid_student_ids:
                    continue

                cat_val = Decimal(str(item.get('coursework_mark', 0) or 0))
                exam_val = Decimal(str(item.get('exam_mark', 0) or 0))

                # Bounds checking
                if cat_val < 0 or cat_val > BulkMarkUploadService.MAX_COURSEWORK_MARK:
                    raise ValidationError(f"CAT mark {cat_val} exceeds maximum allowed ({BulkMarkUploadService.MAX_COURSEWORK_MARK}).")
                if exam_val < 0 or exam_val > BulkMarkUploadService.MAX_EXAM_MARK:
                    raise ValidationError(f"Exam mark {exam_val} exceeds maximum allowed ({BulkMarkUploadService.MAX_EXAM_MARK}).")

                mark_obj, created = StudentMark.objects.update_or_create(
                    student_id=student_id,
                    examination=submission.examination,
                    defaults={
                        'coursework_mark': cat_val,
                        'exam_mark': exam_val,
                        'submitted_by': submission.lecturer,
                        'submission': submission,
                        'status': 'DRAFT'
                    }
                )
                saved_count += 1

            ResultWorkflowAudit.objects.create(
                submission=submission,
                actor=actor,
                action='DRAFT_SAVED',
                previous_status=submission.status,
                new_status=submission.status,
                comment=f"Saved {saved_count} draft marks manually."
            )

        return saved_count

    @classmethod
    def submit_to_cod(cls, submission, actor, comments=""):
        """
        Submits marks from Lecturer to COD.
        Executes reconciliation engine; blocks if critical anomalies exist.
        """
        allowed_statuses = ['DRAFT', 'COD_REJECTED', 'DEAN_REJECTED', 'FINAL_REJECTED']
        if submission.status not in allowed_statuses:
            raise ValidationError(f"Cannot submit results from current status: {submission.get_status_display()}")

        # Ensure marks have been uploaded/entered
        mark_count = StudentMark.objects.filter(submission=submission).count()
        if mark_count == 0:
            # Also check if marks are linked to examination
            exam_mark_count = StudentMark.objects.filter(examination=submission.examination).count()
            if exam_mark_count == 0:
                raise ValidationError("Cannot submit empty results. Please enter or upload student marks first.")
            else:
                StudentMark.objects.filter(examination=submission.examination).update(submission=submission)

        # Run 3-way reconciliation engine
        recon_engine = ExamReconciliationEngine(submission.examination_id, generated_by=actor)
        recon_result = recon_engine.run_reconciliation()

        # Check for CRITICAL anomalies that block submission
        report_status = recon_result.get('status')
        anomalies_list = recon_result.get('anomalies', [])
        critical_anomalies = [a for a in anomalies_list if a.get('severity') == 'CRITICAL']

        if critical_anomalies or report_status == 'FLAGGED_CRITICAL':
            critical_details = "; ".join([f"{a.get('type_display', a.get('anomaly_type'))}: {a.get('description')}" for a in critical_anomalies[:3]])
            raise ValidationError(
                f"Submission blocked: Critical reconciliation anomalies detected ({len(critical_anomalies)}). "
                f"Details: {critical_details}. Please resolve with examination office before submission."
            )

        prev_status = submission.status
        with transaction.atomic():
            submission.status = 'SUBMITTED'
            submission.submitted_at = timezone.now()
            submission.comments = comments
            submission.save()

            # Update marks status
            StudentMark.objects.filter(examination=submission.examination).update(
                submission=submission,
                status='SUBMITTED'
            )

            ResultWorkflowAudit.objects.create(
                submission=submission,
                actor=actor,
                action='SUBMITTED',
                previous_status=prev_status,
                new_status='SUBMITTED',
                comment=comments or "Marks submitted for Chairman of Department (COD) review."
            )

        return submission

    @classmethod
    def cod_review(cls, submission, actor, action, comments="", rejection_reason=""):
        """
        COD review transition: SUBMITTED -> COD_APPROVED or COD_REJECTED.
        """
        if submission.status != 'SUBMITTED':
            raise ValidationError(f"Submission is not pending COD review (Current: {submission.get_status_display()}).")

        # Object-level verification: User must be COD of this department or Admin
        if not cls._is_cod_of_department(actor, submission.department) and not actor.is_superuser:
            raise ValidationError("Permission Denied: You are not authorized as the COD for this department.")

        prev_status = submission.status
        with transaction.atomic():
            if action.upper() == 'APPROVE':
                submission.status = 'COD_APPROVED'
                submission.cod_reviewed_by = actor
                submission.cod_reviewed_at = timezone.now()
                submission.rejection_reason = None
                submission.save()

                ResultWorkflowAudit.objects.create(
                    submission=submission,
                    actor=actor,
                    action='COD_APPROVED',
                    previous_status=prev_status,
                    new_status='COD_APPROVED',
                    comment=comments or "Approved by Chairman of Department."
                )
            elif action.upper() == 'REJECT':
                if not rejection_reason.strip():
                    raise ValidationError("Rejection reason is mandatory when rejecting results.")
                submission.status = 'COD_REJECTED'
                submission.cod_reviewed_by = actor
                submission.cod_reviewed_at = timezone.now()
                submission.rejection_reason = rejection_reason
                submission.save()

                # Revert mark statuses to DRAFT for lecturer correction
                StudentMark.objects.filter(submission=submission).update(status='DRAFT')

                ResultWorkflowAudit.objects.create(
                    submission=submission,
                    actor=actor,
                    action='COD_REJECTED',
                    previous_status=prev_status,
                    new_status='COD_REJECTED',
                    comment=f"Rejected by COD. Reason: {rejection_reason}"
                )
            else:
                raise ValidationError("Invalid action. Must be APPROVE or REJECT.")

        return submission

    @classmethod
    def dean_review(cls, submission, actor, action, comments="", rejection_reason=""):
        """
        Dean review transition: COD_APPROVED -> DEAN_APPROVED or DEAN_REJECTED.
        """
        if submission.status != 'COD_APPROVED':
            raise ValidationError(f"Submission is not pending Dean review (Current: {submission.get_status_display()}).")

        # Object-level verification: User must be Dean of this school or Admin
        if not cls._is_dean_of_school(actor, submission.school) and not actor.is_superuser:
            raise ValidationError("Permission Denied: You are not authorized as the Dean for this school.")

        prev_status = submission.status
        with transaction.atomic():
            if action.upper() == 'APPROVE':
                submission.status = 'DEAN_APPROVED'
                submission.dean_reviewed_by = actor
                submission.dean_reviewed_at = timezone.now()
                submission.rejection_reason = None
                submission.save()

                ResultWorkflowAudit.objects.create(
                    submission=submission,
                    actor=actor,
                    action='DEAN_APPROVED',
                    previous_status=prev_status,
                    new_status='DEAN_APPROVED',
                    comment=comments or "Approved by Dean of School."
                )
            elif action.upper() == 'REJECT':
                if not rejection_reason.strip():
                    raise ValidationError("Rejection reason is mandatory when rejecting results.")
                submission.status = 'DEAN_REJECTED'
                submission.dean_reviewed_by = actor
                submission.dean_reviewed_at = timezone.now()
                submission.rejection_reason = rejection_reason
                submission.save()

                # Revert marks to DRAFT
                StudentMark.objects.filter(submission=submission).update(status='DRAFT')

                ResultWorkflowAudit.objects.create(
                    submission=submission,
                    actor=actor,
                    action='DEAN_REJECTED',
                    previous_status=prev_status,
                    new_status='DEAN_REJECTED',
                    comment=f"Rejected by Dean. Reason: {rejection_reason}"
                )
            else:
                raise ValidationError("Invalid action. Must be APPROVE or REJECT.")

        return submission

    @classmethod
    def final_review_and_publish(cls, submission, actor, action, comments="", rejection_reason=""):
        """
        Final Examination Authority publication: DEAN_APPROVED -> PUBLISHED or FINAL_REJECTED.
        """
        if submission.status != 'DEAN_APPROVED':
            raise ValidationError(f"Submission is not pending final publication (Current: {submission.get_status_display()}).")

        # Object-level verification: User must have EXAM_OFFICER or ADMIN role
        if not cls._is_exam_officer_or_admin(actor):
            raise ValidationError("Permission Denied: You must be an Examination Officer or System Administrator to publish results.")

        prev_status = submission.status
        with transaction.atomic():
            if action.upper() in ['APPROVE', 'PUBLISH']:
                submission.status = 'PUBLISHED'
                submission.published_by = actor
                submission.published_at = timezone.now()
                submission.rejection_reason = None
                submission.save()

                # Update marks to PUBLISHED so they become accessible in the student portal
                StudentMark.objects.filter(examination=submission.examination).update(
                    submission=submission,
                    status='PUBLISHED'
                )

                ResultWorkflowAudit.objects.create(
                    submission=submission,
                    actor=actor,
                    action='PUBLISHED',
                    previous_status=prev_status,
                    new_status='PUBLISHED',
                    comment=comments or "Official examination results approved and published to Student Portal."
                )
            elif action.upper() == 'REJECT':
                if not rejection_reason.strip():
                    raise ValidationError("Rejection reason is mandatory when rejecting results.")
                submission.status = 'FINAL_REJECTED'
                submission.published_by = actor
                submission.rejection_reason = rejection_reason
                submission.save()

                # Revert marks to DRAFT
                StudentMark.objects.filter(submission=submission).update(status='DRAFT')

                ResultWorkflowAudit.objects.create(
                    submission=submission,
                    actor=actor,
                    action='FINAL_REJECTED',
                    previous_status=prev_status,
                    new_status='FINAL_REJECTED',
                    comment=f"Rejected by Examination Authority. Reason: {rejection_reason}"
                )
            else:
                raise ValidationError("Invalid action. Must be PUBLISH or REJECT.")

        return submission

    @classmethod
    def _is_cod_of_department(cls, user, department):
        if user.is_superuser:
            return True
        user_roles = user.user_roles.values_list('role__name', flat=True)
        if Role.COD in user_roles or Role.ADMIN in user_roles:
            lecturer = getattr(user, 'lecturer_profile', None)
            if lecturer and lecturer.department_id == department.id:
                return True
            if Role.ADMIN in user_roles:
                return True
        return False

    @classmethod
    def _is_dean_of_school(cls, user, school):
        if user.is_superuser:
            return True
        user_roles = user.user_roles.values_list('role__name', flat=True)
        if Role.DEAN in user_roles or Role.ADMIN in user_roles:
            lecturer = getattr(user, 'lecturer_profile', None)
            if lecturer and lecturer.department.school_id == school.id:
                return True
            if Role.ADMIN in user_roles:
                return True
        return False

    @classmethod
    def _is_exam_officer_or_admin(cls, user):
        if user.is_superuser or user.is_staff:
            return True
        user_roles = set(user.user_roles.values_list('role__name', flat=True))
        return bool(user_roles & {Role.EXAM_OFFICER, Role.ADMIN})
