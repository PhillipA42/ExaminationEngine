import logging
from datetime import datetime, date, time
from typing import List, Dict, Any, Optional, Tuple
from django.utils import timezone
from django.db.models import Q
from academics.models import UnitRegistration, Student
from locations.models import Room
from .models import (
    ExaminationPeriod, Examination, ExamSchedule,
    ExamRoomAllocation, StudentExamAllocation, ExamTimeSlot
)

logger = logging.getLogger(__name__)


class ConstraintViolation:
    """Represents a discrete hard or soft constraint violation with rich context."""
    def __init__(
        self,
        constraint_type: str,
        severity: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        self.constraint_type = constraint_type # e.g. 'STUDENT_CLASH', 'ROOM_CLASH'
        self.severity = severity # 'HARD' or 'SOFT'
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'constraint_type': self.constraint_type,
            'severity': self.severity,
            'message': self.message,
            'details': self.details,
        }

    def __str__(self):
        return f"[{self.severity}] {self.constraint_type}: {self.message}"


class ConflictReport:
    """Structured report collecting all hard violations and soft warnings for a schedule or period."""
    def __init__(self):
        self.hard_violations: List[ConstraintViolation] = []
        self.soft_warnings: List[ConstraintViolation] = []

    @property
    def is_valid(self) -> bool:
        """A timetable is valid if and only if zero hard constraints are violated."""
        return len(self.hard_violations) == 0

    def add_violation(
        self,
        constraint_type: str,
        severity: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> ConstraintViolation:
        violation = ConstraintViolation(constraint_type, severity, message, details)
        if severity.upper() == 'HARD':
            self.hard_violations.append(violation)
        else:
            self.soft_warnings.append(violation)
        return violation

    def merge(self, other: 'ConflictReport'):
        self.hard_violations.extend(other.hard_violations)
        self.soft_warnings.extend(other.soft_warnings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'is_valid': self.is_valid,
            'hard_violations_count': len(self.hard_violations),
            'soft_warnings_count': len(self.soft_warnings),
            'hard_violations': [v.to_dict() for v in self.hard_violations],
            'soft_warnings': [w.to_dict() for w in self.soft_warnings],
            'summary': {
                'student_clashes': sum(1 for v in self.hard_violations if v.constraint_type == 'STUDENT_CLASH'),
                'room_clashes': sum(1 for v in self.hard_violations if v.constraint_type == 'ROOM_CLASH'),
                'capacity_exceeded': sum(1 for v in self.hard_violations if v.constraint_type == 'ROOM_CAPACITY_EXCEEDED'),
                'unregistered_students': sum(1 for v in self.hard_violations if v.constraint_type == 'UNREGISTERED_STUDENT'),
                'period_bounds': sum(1 for v in self.hard_violations if v.constraint_type == 'PERIOD_DATE_OUT_OF_BOUNDS'),
            }
        }


class ExamConstraintChecker:
    """
    Centralized constraint auditing service.
    Enforces the university's 8 mandatory hard constraints and optimizes soft preferences.
    """

    @staticmethod
    def check_time_boundaries(
        examination: Examination,
        exam_date: date,
        start_time: time,
        end_time: time,
        report: Optional[ConflictReport] = None
    ) -> ConflictReport:
        """Validates date bounds and start/end time validity."""
        report = report or ConflictReport()
        period = examination.period

        # 1. Start time must precede end time
        if start_time >= end_time:
            report.add_violation(
                constraint_type='INVALID_TIME_RANGE',
                severity='HARD',
                message=f"Start time ({start_time}) must be strictly before end time ({end_time}).",
                details={
                    'examination_id': examination.id,
                    'unit_code': examination.unit.code,
                    'start_time': str(start_time),
                    'end_time': str(end_time),
                }
            )

        # 2. Date must fall within the Examination Period
        if exam_date < period.start_date or exam_date > period.end_date:
            report.add_violation(
                constraint_type='PERIOD_DATE_OUT_OF_BOUNDS',
                severity='HARD',
                message=(
                    f"Exam date {exam_date} is outside the examination period window "
                    f"({period.start_date} to {period.end_date})."
                ),
                details={
                    'examination_id': examination.id,
                    'unit_code': examination.unit.code,
                    'exam_date': str(exam_date),
                    'period_start': str(period.start_date),
                    'period_end': str(period.end_date),
                }
            )

        return report

    @staticmethod
    def check_student_clashes(
        examination: Examination,
        exam_date: date,
        start_time: time,
        end_time: time,
        exclude_examination_id: Optional[int] = None,
        report: Optional[ConflictReport] = None
    ) -> ConflictReport:
        """
        Hard Constraint 1: A student must never be scheduled for two examinations
        whose time periods overlap.
        """
        report = report or ConflictReport()
        period = examination.period

        # 1. Fetch all students registered for this unit
        registered_students_qs = UnitRegistration.objects.filter(
            unit=examination.unit,
            academic_year=period.academic_year,
            semester=period.semester,
            registration_status='REGISTERED'
        ).select_related('student__user')

        registered_student_ids = set(registered_students_qs.values_list('student_id', flat=True))
        if not registered_student_ids:
            return report

        # 2. Find overlapping schedules on the same date
        overlapping_schedules = ExamSchedule.objects.filter(
            exam_date=exam_date,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).select_related('examination__unit', 'examination__period')

        if exclude_examination_id:
            overlapping_schedules = overlapping_schedules.exclude(examination_id=exclude_examination_id)
        else:
            overlapping_schedules = overlapping_schedules.exclude(examination_id=examination.id)

        # 3. Check student intersection
        for other_schedule in overlapping_schedules:
            other_exam = other_schedule.examination
            other_students_qs = UnitRegistration.objects.filter(
                unit=other_exam.unit,
                academic_year=other_exam.period.academic_year,
                semester=other_exam.period.semester,
                registration_status='REGISTERED'
            ).select_related('student__user')

            other_student_map = {s.student_id: s.student for s in other_students_qs}
            common_ids = registered_student_ids.intersection(other_student_map.keys())

            for clashing_id in common_ids:
                student_obj = other_student_map[clashing_id]
                reg_no = student_obj.registration_number
                name = student_obj.user.get_full_name() or student_obj.user.username
                report.add_violation(
                    constraint_type='STUDENT_CLASH',
                    severity='HARD',
                    message=(
                        f"Student clash: {reg_no} ({name}) is scheduled for {examination.unit.code} "
                        f"and {other_exam.unit.code} simultaneously on {exam_date} "
                        f"between {max(start_time, other_schedule.start_time)} and {min(end_time, other_schedule.end_time)}."
                    ),
                    details={
                        'student_id': clashing_id,
                        'student_registration_number': reg_no,
                        'student_name': name,
                        'examination_id_1': examination.id,
                        'unit_code_1': examination.unit.code,
                        'examination_id_2': other_exam.id,
                        'unit_code_2': other_exam.unit.code,
                        'exam_date': str(exam_date),
                        'slot_1': f"{start_time} - {end_time}",
                        'slot_2': f"{other_schedule.start_time} - {other_schedule.end_time}",
                    }
                )

        return report

    @staticmethod
    def check_room_clashes(
        room: Room,
        exam_date: date,
        start_time: time,
        end_time: time,
        exclude_examination_id: Optional[int] = None,
        report: Optional[ConflictReport] = None
    ) -> ConflictReport:
        """
        Hard Constraint 2: A room must never be assigned to two examinations
        whose time periods overlap.
        """
        report = report or ConflictReport()

        if room.status != 'ACTIVE':
            report.add_violation(
                constraint_type='ROOM_UNAVAILABLE',
                severity='HARD',
                message=f"Room '{room.name}' ({room.room_number}) is not ACTIVE.",
                details={'room_id': room.id, 'room_name': room.name, 'status': room.status}
            )

        overlapping_allocations = ExamRoomAllocation.objects.filter(
            room=room,
            examination__schedule__exam_date=exam_date,
            examination__schedule__start_time__lt=end_time,
            examination__schedule__end_time__gt=start_time
        ).select_related('examination__unit', 'examination__schedule')

        if exclude_examination_id:
            overlapping_allocations = overlapping_allocations.exclude(examination_id=exclude_examination_id)

        for alloc in overlapping_allocations:
            other_exam = alloc.examination
            other_sched = other_exam.schedule
            report.add_violation(
                constraint_type='ROOM_CLASH',
                severity='HARD',
                message=(
                    f"Room double-booking: '{room.name}' is already allocated to {other_exam.unit.code} "
                    f"on {exam_date} ({other_sched.start_time} - {other_sched.end_time})."
                ),
                details={
                    'room_id': room.id,
                    'room_name': room.name,
                    'conflicting_examination_id': other_exam.id,
                    'conflicting_unit_code': other_exam.unit.code,
                    'exam_date': str(exam_date),
                    'existing_start': str(other_sched.start_time),
                    'existing_end': str(other_sched.end_time),
                }
            )

        return report

    @staticmethod
    def check_room_capacity(
        examination: Examination,
        room_allocations: List[Dict[str, Any]],
        report: Optional[ConflictReport] = None
    ) -> ConflictReport:
        """
        Hard Constraint 3: The number of students allocated to a room must never
        exceed the room capacity. Total room capacity must cover registered students.
        """
        report = report or ConflictReport()
        period = examination.period

        total_registered = UnitRegistration.objects.filter(
            unit=examination.unit,
            academic_year=period.academic_year,
            semester=period.semester,
            registration_status='REGISTERED'
        ).count()

        total_allocated_capacity = 0

        for item in room_allocations:
            room = item['room'] if isinstance(item['room'], Room) else Room.objects.get(id=item['room'])
            allocated_count = item.get('allocated_capacity', 0)

            if room.status != 'ACTIVE':
                report.add_violation(
                    constraint_type='ROOM_UNAVAILABLE',
                    severity='HARD',
                    message=f"Room '{room.name}' is inactive.",
                    details={'room_id': room.id, 'room_name': room.name}
                )

            if allocated_count > room.capacity:
                report.add_violation(
                    constraint_type='ROOM_CAPACITY_EXCEEDED',
                    severity='HARD',
                    message=(
                        f"Room capacity exceeded in '{room.name}': allocated {allocated_count} students "
                        f"into a room with capacity {room.capacity}."
                    ),
                    details={
                        'room_id': room.id,
                        'room_name': room.name,
                        'room_capacity': room.capacity,
                        'allocated_students': allocated_count,
                        'excess': allocated_count - room.capacity
                    }
                )

            total_allocated_capacity += allocated_count

        if total_registered > 0 and total_allocated_capacity < total_registered:
            report.add_violation(
                constraint_type='INSUFFICIENT_ROOM_CAPACITY',
                severity='HARD',
                message=(
                    f"Insufficient room capacity for {examination.unit.code}: {total_registered} students registered, "
                    f"but allocated rooms only seat {total_allocated_capacity} students."
                ),
                details={
                    'examination_id': examination.id,
                    'unit_code': examination.unit.code,
                    'total_registered': total_registered,
                    'total_allocated_capacity': total_allocated_capacity,
                    'shortfall': total_registered - total_allocated_capacity
                }
            )

        return report

    @staticmethod
    def check_student_eligibility_and_duplication(
        examination: Examination,
        student_allocations: List[Dict[str, Any]],
        report: Optional[ConflictReport] = None
    ) -> ConflictReport:
        """
        Hard Constraint 4: A student may only be allocated to an examination for a unit
        for which the student has a valid UnitRegistration.
        Hard Constraint 8: A student must not receive duplicate allocation for the same examination.
        """
        report = report or ConflictReport()
        period = examination.period

        valid_student_ids = set(
            UnitRegistration.objects.filter(
                unit=examination.unit,
                academic_year=period.academic_year,
                semester=period.semester,
                registration_status='REGISTERED'
            ).values_list('student_id', flat=True)
        )

        seen_students = set()

        for alloc in student_allocations:
            student_id = alloc.get('student_id')
            if not student_id and 'student' in alloc:
                student_id = alloc['student'].id if hasattr(alloc['student'], 'id') else alloc['student']

            # Check duplication
            if student_id in seen_students:
                report.add_violation(
                    constraint_type='DUPLICATE_STUDENT_ALLOCATION',
                    severity='HARD',
                    message=f"Student ID {student_id} is allocated more than once for {examination.unit.code}.",
                    details={'student_id': student_id, 'examination_id': examination.id}
                )
            seen_students.add(student_id)

            # Check registration validity
            if student_id not in valid_student_ids:
                student_obj = Student.objects.filter(id=student_id).first()
                reg_no = student_obj.registration_number if student_obj else str(student_id)
                report.add_violation(
                    constraint_type='UNREGISTERED_STUDENT',
                    severity='HARD',
                    message=(
                        f"Student {reg_no} does not have a valid active registration for "
                        f"{examination.unit.code} in {period.academic_year} Semester {period.semester}."
                    ),
                    details={
                        'student_id': student_id,
                        'student_registration_number': reg_no,
                        'examination_id': examination.id,
                        'unit_code': examination.unit.code
                    }
                )

        return report

    @classmethod
    def validate_manual_schedule(
        cls,
        examination: Examination,
        exam_date: date,
        start_time: time,
        end_time: time,
        room_allocations: Optional[List[Dict[str, Any]]] = None,
        student_allocations: Optional[List[Dict[str, Any]]] = None,
        exclude_current: bool = True
    ) -> ConflictReport:
        """
        Validates a full manual scheduling request against all hard constraints.
        Returns a complete ConflictReport.
        """
        report = ConflictReport()
        exclude_id = examination.id if exclude_current else None

        # 1. Time bounds
        cls.check_time_boundaries(examination, exam_date, start_time, end_time, report)

        # 2. Student Clashes
        cls.check_student_clashes(examination, exam_date, start_time, end_time, exclude_id, report)

        # 3. Room Clashes
        if room_allocations:
            for item in room_allocations:
                room = item['room'] if isinstance(item['room'], Room) else Room.objects.get(id=item['room'])
                cls.check_room_clashes(room, exam_date, start_time, end_time, exclude_id, report)

            # 4. Room Capacity
            cls.check_room_capacity(examination, room_allocations, report)

        # 5. Student Allocations
        if student_allocations:
            cls.check_student_eligibility_and_duplication(examination, student_allocations, report)

        return report

    @classmethod
    def validate_period_entire_timetable(cls, period: ExaminationPeriod) -> ConflictReport:
        """
        Exhaustive multi-exam system audit for an entire ExaminationPeriod.
        Checks all examinations, schedules, room allocations, and student allocations.
        """
        report = ConflictReport()
        examinations = list(period.examinations.select_related('unit', 'schedule').prefetch_related('room_allocations__room'))

        # Track rooms and students for cross-exam analysis
        scheduled_exams = [e for e in examinations if hasattr(e, 'schedule')]

        for exam in scheduled_exams:
            sched = exam.schedule
            # Check individual time bounds
            cls.check_time_boundaries(exam, sched.exam_date, sched.start_time, sched.end_time, report)

            # Check room capacities
            room_allocs = [{'room': ra.room, 'allocated_capacity': ra.allocated_capacity} for ra in exam.room_allocations.all()]
            # An empty allocation is itself insufficient for a non-empty cohort.
            cls.check_room_capacity(exam, room_allocs, report)

        # Cross-Exam Student Clashes
        for i in range(len(scheduled_exams)):
            for j in range(i + 1, len(scheduled_exams)):
                exam_a = scheduled_exams[i]
                exam_b = scheduled_exams[j]
                sched_a = exam_a.schedule
                sched_b = exam_b.schedule

                # Check date overlap
                if sched_a.exam_date == sched_b.exam_date:
                    # Check time overlap
                    if sched_a.start_time < sched_b.end_time and sched_a.end_time > sched_b.start_time:
                        # Find overlapping students
                        studs_a = set(UnitRegistration.objects.filter(
                            unit=exam_a.unit, academic_year=period.academic_year, semester=period.semester, registration_status='REGISTERED'
                        ).values_list('student_id', flat=True))
                        studs_b = set(UnitRegistration.objects.filter(
                            unit=exam_b.unit, academic_year=period.academic_year, semester=period.semester, registration_status='REGISTERED'
                        ).values_list('student_id', flat=True))

                        clashes = studs_a.intersection(studs_b)
                        for cid in clashes:
                            st = Student.objects.filter(id=cid).first()
                            reg_no = st.registration_number if st else str(cid)
                            report.add_violation(
                                constraint_type='STUDENT_CLASH',
                                severity='HARD',
                                message=(
                                    f"Student clash: {reg_no} is scheduled for both {exam_a.unit.code} "
                                    f"and {exam_b.unit.code} on {sched_a.exam_date} simultaneously."
                                ),
                                details={
                                    'student_id': cid,
                                    'student_reg_no': reg_no,
                                    'unit_a': exam_a.unit.code,
                                    'unit_b': exam_b.unit.code,
                                    'date': str(sched_a.exam_date)
                                }
                            )

                    # Soft Constraint Warning: Multiple exams for same student on same day
                    else:
                        studs_a = set(UnitRegistration.objects.filter(
                            unit=exam_a.unit, academic_year=period.academic_year, semester=period.semester, registration_status='REGISTERED'
                        ).values_list('student_id', flat=True))
                        studs_b = set(UnitRegistration.objects.filter(
                            unit=exam_b.unit, academic_year=period.academic_year, semester=period.semester, registration_status='REGISTERED'
                        ).values_list('student_id', flat=True))

                        same_day_overlap = studs_a.intersection(studs_b)
                        if same_day_overlap:
                            report.add_violation(
                                constraint_type='STUDENT_DOUBLE_EXAM_SAME_DAY',
                                severity='SOFT',
                                message=(
                                    f"Student fatigue: {len(same_day_overlap)} student(s) have two examinations "
                                    f"({exam_a.unit.code} and {exam_b.unit.code}) on the same day ({sched_a.exam_date})."
                                ),
                                details={
                                    'affected_students_count': len(same_day_overlap),
                                    'unit_a': exam_a.unit.code,
                                    'unit_b': exam_b.unit.code,
                                    'date': str(sched_a.exam_date)
                                }
                            )

        # Cross-Exam Room Clashes
        room_allocations_all = ExamRoomAllocation.objects.filter(
            examination__period=period
        ).select_related('room', 'examination__unit', 'examination__schedule')

        alloc_list = list(room_allocations_all)
        for i in range(len(alloc_list)):
            for j in range(i + 1, len(alloc_list)):
                ra_1 = alloc_list[i]
                ra_2 = alloc_list[j]

                if ra_1.room_id == ra_2.room_id:
                    s1 = getattr(ra_1.examination, 'schedule', None)
                    s2 = getattr(ra_2.examination, 'schedule', None)
                    if s1 and s2 and s1.exam_date == s2.exam_date:
                        if s1.start_time < s2.end_time and s1.end_time > s2.start_time:
                            report.add_violation(
                                constraint_type='ROOM_CLASH',
                                severity='HARD',
                                message=(
                                    f"Room double-booking: Room '{ra_1.room.name}' is assigned to both "
                                    f"{ra_1.examination.unit.code} and {ra_2.examination.unit.code} on "
                                    f"{s1.exam_date} ({max(s1.start_time, s2.start_time)} - {min(s1.end_time, s2.end_time)})."
                                ),
                                details={
                                    'room_id': ra_1.room_id,
                                    'room_name': ra_1.room.name,
                                    'unit_1': ra_1.examination.unit.code,
                                    'unit_2': ra_2.examination.unit.code,
                                    'date': str(s1.exam_date)
                                }
                            )

        # Student allocation integrity
        student_allocs = StudentExamAllocation.objects.filter(
            examination__period=period
        ).select_related('examination__unit', 'student')
        seen_pairs = set()
        for sa in student_allocs:
            pair = (sa.examination_id, sa.student_id)
            if pair in seen_pairs:
                report.add_violation(
                    constraint_type='DUPLICATE_STUDENT_ALLOCATION',
                    severity='HARD',
                    message=f"Duplicate allocation: Student ID {sa.student_id} allocated twice to exam {sa.examination_id}.",
                    details={'student_id': sa.student_id, 'examination_id': sa.examination_id}
                )
            seen_pairs.add(pair)

            if not UnitRegistration.objects.filter(
                student_id=sa.student_id,
                unit=sa.examination.unit,
                academic_year=period.academic_year,
                semester=period.semester,
                registration_status='REGISTERED',
            ).exists():
                report.add_violation(
                    constraint_type='UNREGISTERED_STUDENT', severity='HARD',
                    message=(f"Student {sa.student.registration_number} is allocated to "
                             f"{sa.examination.unit.code} without a valid unit registration."),
                    details={'student_id': sa.student_id, 'examination_id': sa.examination_id,
                             'unit_code': sa.examination.unit.code},
                )

        return report

    @classmethod
    def publish_timetable(cls, period: ExaminationPeriod, published_by_user) -> Tuple[bool, ConflictReport]:
        """
        Guarded publication workflow.
        A timetable can ONLY be published if an exhaustive constraint audit passes with zero hard violations.
        """
        report = cls.validate_period_entire_timetable(period)
        if not report.is_valid:
            logger.warning(
                f"Publication rejected for {period.name}: {len(report.hard_violations)} hard constraints violated."
            )
            return False, report

        now = timezone.now()
        period.status = 'PUBLISHED'
        period.save()

        # Update all schedules
        ExamSchedule.objects.filter(examination__period=period).update(
            status='PUBLISHED',
            published_at=now,
            published_by=published_by_user
        )

        logger.info(f"Timetable for {period.name} successfully published by {published_by_user}.")
        return True, report
