from collections import defaultdict
from django.db import transaction
from django.utils import timezone

from scheduling.models import Examination, ExaminationPeriod
from invigilators.models import ExamAttendance
from malpractice.models import MalpracticeCase
from .models import Student, UnitRegistration, StudentMark, ReconciliationReport, ReconciliationAnomaly


class ExamReconciliationEngine:
    """
    Automated Examination Reconciliation Engine.
    Cross-references UnitRegistration, live ExamAttendance (physical booklets), and uploaded StudentMarks
    to detect integrity anomalies, missing booklets, unregistered examinees, ghost marks, and unsubmitted marks.
    """

    def __init__(self, examination_id, generated_by=None):
        self.examination = Examination.objects.select_related(
            'unit__course__department__school',
            'period'
        ).get(id=examination_id)
        self.generated_by = generated_by

    def run_reconciliation(self):
        """
        Executes 3-way reconciliation analysis and persists a ReconciliationReport with detected anomalies.
        """
        unit = self.examination.unit
        period = self.examination.period

        # 1. Fetch Registered Students (Official Enrollment Roster)
        registrations = UnitRegistration.objects.filter(
            unit=unit,
            academic_year=period.academic_year,
            semester=period.semester,
            registration_status='REGISTERED'
        ).select_related('student__user')
        registered_student_ids = set(registrations.values_list('student_id', flat=True))
        registered_students_map = {r.student_id: r.student for r in registrations}

        # 2. Fetch Live Examination Attendance Records (Physical Booklets)
        attendances = ExamAttendance.objects.filter(
            examination=self.examination
        ).select_related('student__user', 'recorded_by__user', 'room')
        
        present_attendances = [a for a in attendances if a.is_present]
        absent_attendances = [a for a in attendances if not a.is_present]

        attended_present_ids = set(a.student_id for a in present_attendances)
        attended_absent_ids = set(a.student_id for a in absent_attendances)
        all_attendance_ids = set(a.student_id for a in attendances)
        attendance_map = {a.student_id: a for a in attendances}

        # 3. Fetch Uploaded Marks
        marks = StudentMark.objects.filter(
            examination=self.examination
        ).select_related('student__user')
        marks_student_ids = set(marks.values_list('student_id', flat=True))
        marks_map = {m.student_id: m for m in marks}

        # 4. Fetch Malpractice Cases
        malpractice_cases = MalpracticeCase.objects.filter(
            examination=self.examination
        ).select_related('student__user')
        malpractice_map = {mc.student_id: mc for mc in malpractice_cases}

        # 5. Anomaly Detection
        anomalies_to_create = []

        # Map for resolving student objects across sources
        all_involved_student_ids = (
            registered_student_ids | attended_present_ids | attended_absent_ids | marks_student_ids
        )
        students_db_map = {
            s.id: s for s in Student.objects.filter(id__in=all_involved_student_ids).select_related('user', 'course')
        }

        # -------------------------------------------------------------
        # Rule 1: UNREGISTERED EXAMINEES
        # Student attended exam with booklet OR has marks submitted, but is NOT officially registered.
        # -------------------------------------------------------------
        unregistered_present = (attended_present_ids | marks_student_ids) - registered_student_ids
        for s_id in unregistered_present:
            student = students_db_map.get(s_id)
            att = attendance_map.get(s_id)
            mark = marks_map.get(s_id)
            
            evidence = []
            if att:
                evidence.append(f"Physical Booklet: #{att.booklet_serial_number} (Room: {att.room.name if att.room else 'Unknown'})")
            if mark:
                evidence.append(f"Uploaded Mark: {mark.total_mark} ({mark.grade})")

            anomalies_to_create.append({
                'student': student,
                'anomaly_type': 'UNREGISTERED_EXAMINEE',
                'severity': 'CRITICAL',
                'description': (
                    f"Candidate {student.registration_number if student else f'ID {s_id}'} sat for the examination "
                    f"or had marks submitted, but is NOT officially registered for {unit.code} "
                    f"in {period.academic_year} Semester {period.semester}."
                ),
                'evidence_summary': " | ".join(evidence) or "Unregistered entry found in exam data.",
            })

        # -------------------------------------------------------------
        # Rule 2: GHOST MARKS (Marks Uploaded for Absent / Non-Attending Students)
        # Student marked absent or never checked in, but has uploaded exam marks.
        # -------------------------------------------------------------
        for s_id in marks_student_ids:
            if s_id in attended_absent_ids or s_id not in all_attendance_ids:
                student = students_db_map.get(s_id)
                mark = marks_map.get(s_id)
                att = attendance_map.get(s_id)
                att_status = "Marked ABSENT in hall" if (att and not att.is_present) else "No attendance record found"

                anomalies_to_create.append({
                    'student': student,
                    'anomaly_type': 'GHOST_MARKS',
                    'severity': 'CRITICAL',
                    'description': (
                        f"Candidate {student.registration_number if student else f'ID {s_id}'} was {att_status}, "
                        f"yet an examination mark of {mark.total_mark} ({mark.grade}) was submitted by the examiner."
                    ),
                    'evidence_summary': f"Exam Mark: {mark.exam_mark}/70, Total: {mark.total_mark}/100, Attendance Status: {att_status}",
                })

        # -------------------------------------------------------------
        # Rule 3: MISSING PHYSICAL BOOKLET SERIAL
        # Student is marked present OR has marks uploaded, but has no valid physical booklet serial number.
        # -------------------------------------------------------------
        for att in present_attendances:
            s_id = att.student_id
            serial = (att.booklet_serial_number or '').strip()
            if not serial or serial.startswith('ABSENT-'):
                student = students_db_map.get(s_id)
                anomalies_to_create.append({
                    'student': student,
                    'anomaly_type': 'MISSING_PHYSICAL_BOOKLET',
                    'severity': 'HIGH',
                    'description': (
                        f"Candidate {student.registration_number if student else f'ID {s_id}'} was marked present in hall "
                        f"({att.room.name if att.room else 'Exam Room'}), but no valid physical booklet serial number was captured."
                    ),
                    'evidence_summary': f"Recorded By: {att.recorded_by.staff_number if att.recorded_by else 'N/A'}, Check-in Time: {att.check_in_time}",
                })

        for s_id in marks_student_ids:
            if s_id not in attended_present_ids:
                # Handled either by Ghost Marks or missing attendance
                pass

        # -------------------------------------------------------------
        # Rule 4: UNSUBMITTED MARKS FOR ATTENDED CANDIDATES
        # Student sat for exam and turned in booklet, but examiner has not submitted marks.
        # -------------------------------------------------------------
        unsubmitted_attendees = attended_present_ids - marks_student_ids
        for s_id in unsubmitted_attendees:
            student = students_db_map.get(s_id)
            att = attendance_map.get(s_id)
            
            anomalies_to_create.append({
                'student': student,
                'anomaly_type': 'UNSUBMITTED_MARKS',
                'severity': 'MEDIUM',
                'description': (
                    f"Candidate {student.registration_number if student else f'ID {s_id}'} attended the exam with "
                    f"physical booklet #{att.booklet_serial_number if att else 'N/A'}, but no examination marks have been submitted."
                ),
                'evidence_summary': f"Booklet #{att.booklet_serial_number if att else 'N/A'}, Room: {att.room.name if att and att.room else 'N/A'}",
            })

        # -------------------------------------------------------------
        # Rule 5: DUPLICATE PHYSICAL BOOKLET SERIAL NUMBERS
        # Same physical booklet number assigned to multiple distinct students.
        # -------------------------------------------------------------
        serial_to_students = defaultdict(list)
        for att in present_attendances:
            serial = (att.booklet_serial_number or '').strip()
            if serial and not serial.startswith('ABSENT-'):
                serial_to_students[serial].append(att.student)

        for serial, student_list in serial_to_students.items():
            if len(student_list) > 1:
                reg_nums = ", ".join(s.registration_number for s in student_list)
                for s in student_list:
                    anomalies_to_create.append({
                        'student': s,
                        'anomaly_type': 'DUPLICATE_BOOKLET_SERIAL',
                        'severity': 'CRITICAL',
                        'description': (
                            f"Physical booklet serial #{serial} was registered to multiple candidates: {reg_nums}."
                        ),
                        'evidence_summary': f"Conflicting Candidates: {reg_nums}",
                    })

        # -------------------------------------------------------------
        # Rule 6: UNRESOLVED MALPRACTICE CASES
        # Student has an active malpractice case during this session.
        # -------------------------------------------------------------
        for s_id, mcase in malpractice_map.items():
            if mcase.status in ['REPORTED', 'UNDER_INVESTIGATION']:
                student = students_db_map.get(s_id)
                anomalies_to_create.append({
                    'student': student,
                    'anomaly_type': 'UNRESOLVED_MALPRACTICE',
                    'severity': 'HIGH',
                    'description': (
                        f"Candidate {student.registration_number if student else f'ID {s_id}'} has an active malpractice "
                        f"case #{mcase.case_number} ({mcase.incident_type} - {mcase.get_severity_display()}) in status '{mcase.status}'."
                    ),
                    'evidence_summary': f"Case #{mcase.case_number}: {mcase.description[:150]}",
                })

        # -------------------------------------------------------------
        # 6. Save Reconciliation Report & Anomalies
        # -------------------------------------------------------------
        total_registered = len(registered_student_ids)
        total_attended = len(attended_present_ids)
        total_absent = len(attended_absent_ids)
        total_booklets = len([a for a in present_attendances if a.booklet_serial_number and not a.booklet_serial_number.startswith('ABSENT-')])
        total_marks = len(marks_student_ids)
        total_anomalies = len(anomalies_to_create)

        has_critical = any(a['severity'] == 'CRITICAL' for a in anomalies_to_create)
        if total_anomalies == 0:
            report_status = 'CLEAN'
        elif has_critical:
            report_status = 'FLAGGED_CRITICAL'
        else:
            report_status = 'ANOMALIES_DETECTED'

        summary_text = (
            f"Reconciliation completed for {unit.code} ({period.name}). "
            f"{total_registered} Registered, {total_attended} Attended, {total_absent} Absent, "
            f"{total_booklets} Physical Booklets Verified, {total_marks} Marks Uploaded. "
            f"{total_anomalies} Anomaly(ies) detected."
        )

        with transaction.atomic():
            report = ReconciliationReport.objects.create(
                examination=self.examination,
                generated_by=self.generated_by,
                total_registered=total_registered,
                total_attended=total_attended,
                total_absent=total_absent,
                total_booklets_issued=total_booklets,
                total_marks_uploaded=total_marks,
                total_anomalies=total_anomalies,
                status=report_status,
                summary=summary_text,
            )

            created_anomalies = []
            for item in anomalies_to_create:
                anomaly = ReconciliationAnomaly.objects.create(
                    report=report,
                    student=item['student'],
                    anomaly_type=item['anomaly_type'],
                    severity=item['severity'],
                    description=item['description'],
                    evidence_summary=item.get('evidence_summary', ''),
                )
                created_anomalies.append(anomaly)

        return {
            'report_id': report.id,
            'status': report.status,
            'summary': summary_text,
            'total_registered': total_registered,
            'total_attended': total_attended,
            'total_absent': total_absent,
            'total_booklets_issued': total_booklets,
            'total_marks_uploaded': total_marks,
            'total_anomalies': total_anomalies,
            'anomalies': [
                {
                    'id': a.id,
                    'student_reg': a.student.registration_number if a.student else None,
                    'student_name': a.student.user.get_full_name() if a.student else None,
                    'anomaly_type': a.anomaly_type,
                    'type_display': a.get_anomaly_type_display(),
                    'severity': a.severity,
                    'description': a.description,
                    'evidence_summary': a.evidence_summary,
                }
                for a in created_anomalies
            ],
        }
