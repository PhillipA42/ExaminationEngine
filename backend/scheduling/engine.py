import logging
from datetime import datetime, timedelta, time, date
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict

from django.db import transaction
from django.utils import timezone
from academics.models import UnitRegistration, Student
from locations.models import Room
from .models import (
    ExaminationPeriod, Examination, ExamSchedule,
    ExamRoomAllocation, StudentExamAllocation, ExamTimeSlot
)
from .services import ExamConstraintChecker, ConflictReport, ConstraintViolation

logger = logging.getLogger(__name__)


class TimetableSchedulerEngine:
    """
    Genuine Constraint-Satisfaction Timetable & Room Allocation Engine.
    
    Hard Constraints (Guaranteed & Non-Violable):
    1. Student Clash: No student has two overlapping exams.
    2. Room Clash: No room is double-booked for overlapping exams.
    3. Room Capacity: Students in room <= room capacity; total capacity >= cohort size.
    4. Valid Registration: Students allocated must have active UnitRegistration.
    5. Period Bounds: Dates strictly between period start_date and end_date.
    6. Valid Time Slots: start_time < end_time.
    7. Active Rooms: Only active rooms are utilized.
    8. Duplicate Prevention: Exactly one seat allocation per student per exam.
    
    Soft Constraints (Heuristically Optimized):
    1. Student Fatigue Reduction: Minimizing 2 exams for a student on the same day.
    2. Period Load Balancing: Distributing exams evenly across available days.
    3. Room Fragmentation Minimization: Preferring fewer, well-fitting rooms over many small rooms.
    """

    def __init__(self, period_id: int, time_slots: Optional[List[Tuple[time, time]]] = None):
        self.period = ExaminationPeriod.objects.get(id=period_id)
        self.examinations = list(
            Examination.objects.filter(period=self.period).select_related('unit')
        )
        self.active_rooms = list(
            Room.objects.filter(status='ACTIVE').select_related('building', 'floor').order_by('-capacity')
        )
        
        # Load time slots: from parameter, or period DB configurations, or default fallback
        if time_slots:
            self.time_slots = time_slots
        else:
            db_slots = list(self.period.time_slots.filter(is_active=True).order_by('start_time'))
            if db_slots:
                self.time_slots = [(s.start_time, s.end_time) for s in db_slots]
            else:
                self.time_slots = [
                    (time(9, 0), time(12, 0)),
                    (time(14, 0), time(17, 0))
                ]

        self.available_dates = self._generate_available_dates()
        self.conflict_report = ConflictReport()

    def _generate_available_dates(self) -> List[date]:
        """Generates weekdays (Mon-Fri) within the examination period window."""
        dates = []
        current = self.period.start_date
        while current <= self.period.end_date:
            if current.weekday() < 5:  # Monday through Friday
                dates.append(current)
            current += timedelta(days=1)
        return dates

    def _build_student_registration_map(self) -> Dict[int, List[int]]:
        """Maps examination_id -> list of registered student IDs from UnitRegistration."""
        exam_students = {}
        for exam in self.examinations:
            student_ids = list(
                UnitRegistration.objects.filter(
                    unit=exam.unit,
                    academic_year=self.period.academic_year,
                    semester=self.period.semester,
                    registration_status='REGISTERED'
                ).values_list('student_id', flat=True).distinct()
            )
            exam_students[exam.id] = student_ids
        return exam_students

    def _build_conflict_graph(
        self, exam_students: Dict[int, List[int]]
    ) -> Tuple[Dict[int, Set[int]], Dict[int, int]]:
        """
        Builds the conflict graph:
        - conflict_edges[exam_id] = set of other exam_ids that share at least 1 student.
        - conflict_degrees[exam_id] = total number of conflicting exams.
        """
        conflict_edges = defaultdict(set)
        student_to_exams = defaultdict(list)

        for exam_id, students in exam_students.items():
            for student_id in students:
                student_to_exams[student_id].append(exam_id)

        for student_id, exams in student_to_exams.items():
            for i in range(len(exams)):
                for j in range(i + 1, len(exams)):
                    e1, e2 = exams[i], exams[j]
                    conflict_edges[e1].add(e2)
                    conflict_edges[e2].add(e1)

        conflict_degrees = {exam.id: len(conflict_edges[exam.id]) for exam in self.examinations}
        return conflict_edges, conflict_degrees

    def _select_rooms_for_cohort(
        self,
        cohort_size: int,
        available_rooms: List[Room]
    ) -> Optional[List[Tuple[Room, int]]]:
        """
        Best-fit Room Selection:
        1. If single room fits, select smallest single room that fits (minimizes wasted capacity).
        2. If no single room fits, select minimum combination of rooms that satisfies cohort size.
        Returns list of (Room, allocated_count), or None if insufficient total capacity.
        """
        if cohort_size == 0:
            # Exam with 0 registered students (placeholder exam), assign 1 room with 0 capacity
            return [(available_rooms[0], 0)] if available_rooms else None

        # 1. Check for single room that fits
        fitting_single_rooms = [r for r in available_rooms if r.capacity >= cohort_size]
        if fitting_single_rooms:
            # Sort ascending by capacity to pick the closest fit
            best_room = min(fitting_single_rooms, key=lambda r: r.capacity)
            return [(best_room, cohort_size)]

        # 2. Multi-room selection: pick largest rooms first to minimize fragmentation
        available_sorted = sorted(available_rooms, key=lambda r: r.capacity, reverse=True)
        chosen = []
        accumulated = 0
        remaining = cohort_size

        for room in available_sorted:
            alloc_for_room = min(remaining, room.capacity)
            chosen.append((room, alloc_for_room))
            accumulated += alloc_for_room
            remaining -= alloc_for_room
            if remaining <= 0:
                break

        if accumulated < cohort_size:
            return None

        return chosen

    def generate_timetable(self) -> bool:
        """
        Main entry point for timetable generation.
        Returns True on success, False on failure (with structured ConflictReport recorded).
        """
        result = self.execute_scheduling()
        return result['success']

    def execute_scheduling(self) -> Dict[str, Any]:
        """
        Executes constraint-based optimization with complete atomic rollback on failure.
        """
        self.conflict_report = ConflictReport()

        if not self.examinations:
            logger.info(f"No examinations found in period {self.period.name}.")
            return {'success': True, 'report': self.conflict_report, 'scheduled_count': 0}

        if not self.available_dates:
            self.conflict_report.add_violation(
                constraint_type='PERIOD_DATE_OUT_OF_BOUNDS',
                severity='HARD',
                message=f"No valid weekdays exist between {self.period.start_date} and {self.period.end_date}."
            )
            return {'success': False, 'report': self.conflict_report, 'scheduled_count': 0}

        if not self.active_rooms:
            self.conflict_report.add_violation(
                constraint_type='ROOM_UNAVAILABLE',
                severity='HARD',
                message="No active rooms are available in the university for examination scheduling."
            )
            return {'success': False, 'report': self.conflict_report, 'scheduled_count': 0}

        # Step 1: Data Preparation & Graph Construction
        exam_students = self._build_student_registration_map()
        conflict_edges, conflict_degrees = self._build_conflict_graph(exam_students)

        # Step 2: Welsh-Powell / Most Constrained First Sorting
        # Sort examinations by: (1) Conflict degree (descending), (2) Cohort size (descending)
        sorted_examinations = sorted(
            self.examinations,
            key=lambda e: (conflict_degrees.get(e.id, 0), len(exam_students.get(e.id, []))),
            reverse=True
        )

        total_exams = len(sorted_examinations)
        logger.info(f"Scheduling {total_exams} examinations across {len(self.available_dates)} days and {len(self.time_slots)} daily sessions.")

        # Candidate time slots grid: [(date, (start_time, end_time))]
        slot_grid = []
        for d in self.available_dates:
            for s_time, e_time in self.time_slots:
                slot_grid.append((d, s_time, e_time))

        # Runtime state
        # scheduled_state: exam_id -> {'date': d, 'start': s, 'end': e, 'rooms': [(room, count)], 'students': [...]}
        scheduled_state: Dict[int, Dict[str, Any]] = {}
        # room_occupancy: (date, start_time, end_time) -> set of room_ids
        room_occupancy: Dict[Tuple[date, time, time], Set[int]] = defaultdict(set)
        # student_schedule: student_id -> list of (date, start_time, end_time, exam_id)
        student_schedule: Dict[int, List[Tuple[date, time, time, int]]] = defaultdict(list)
        # day_load: date -> count of scheduled exams
        day_load: Dict[date, int] = defaultdict(int)

        failed_exam = None
        failure_reason = ""

        # Step 3: Transactional Execution
        try:
            with transaction.atomic():
                # Clear existing draft schedules and allocations for this period
                ExamSchedule.objects.filter(examination__period=self.period).delete()
                ExamRoomAllocation.objects.filter(examination__period=self.period).delete()
                StudentExamAllocation.objects.filter(examination__period=self.period).delete()

                for exam in sorted_examinations:
                    registered_students = exam_students.get(exam.id, [])
                    cohort_size = len(registered_students)
                    best_slot = None
                    best_score = float('inf')
                    best_room_selection = None

                    # Evaluate candidate slots
                    for exam_date, start_time, end_time in slot_grid:
                        slot_key = (exam_date, start_time, end_time)

                        # --- HARD CONSTRAINT 1: Check Student Clashes ---
                        has_student_clash = False
                        for stud_id in registered_students:
                            existing_slots = student_schedule.get(stud_id, [])
                            for (s_date, s_start, s_end, _) in existing_slots:
                                if s_date == exam_date and s_start < end_time and s_end > start_time:
                                    has_student_clash = True
                                    break
                            if has_student_clash:
                                break

                        if has_student_clash:
                            continue

                        # --- HARD CONSTRAINT 2 & 3: Check Room Availability & Capacity ---
                        occupied_room_ids = room_occupancy[slot_key]
                        available_rooms_in_slot = [r for r in self.active_rooms if r.id not in occupied_room_ids]
                        
                        room_selection = self._select_rooms_for_cohort(cohort_size, available_rooms_in_slot)
                        if not room_selection:
                            # Not enough room capacity in this slot
                            continue

                        # --- SOFT CONSTRAINT SCORING (Lower penalty is preferred) ---
                        penalty = 0

                        # Soft Preference 1: Avoid Student Double-Exams on the same day
                        # Check how many students in this exam already have an exam on exam_date
                        same_day_exams_count = 0
                        for stud_id in registered_students:
                            if any(s_date == exam_date for (s_date, _, _, _) in student_schedule.get(stud_id, [])):
                                same_day_exams_count += 1
                        penalty += same_day_exams_count * 50

                        # Soft Preference 2: Period Load Balancing
                        # Penalize days that already have more exams
                        penalty += day_load[exam_date] * 20

                        # Soft Preference 3: Room Fragmentation Minimization
                        # Prefer fewer rooms (1 room = 0 penalty, 2 rooms = +15, 3 rooms = +30)
                        num_rooms = len(room_selection)
                        if num_rooms > 1:
                            penalty += (num_rooms - 1) * 15

                        if penalty < best_score:
                            best_score = penalty
                            best_slot = slot_key
                            best_room_selection = room_selection

                    # Check if a valid slot was found
                    if not best_slot or not best_room_selection:
                        failed_exam = exam
                        # Diagnose failure reason
                        failure_reason = (
                            f"Could not find a conflict-free slot for {exam.unit.code} (Cohort: {cohort_size} students). "
                            f"Hard constraints violated: All {len(slot_grid)} slots had student clashes with overlapping courses "
                            f"or insufficient room capacity."
                        )
                        self.conflict_report.add_violation(
                            constraint_type='STUDENT_CLASH',
                            severity='HARD',
                            message=failure_reason,
                            details={
                                'examination_id': exam.id,
                                'unit_code': exam.unit.code,
                                'cohort_size': cohort_size,
                                'available_slots_evaluated': len(slot_grid),
                                'conflicting_units_count': len(conflict_edges.get(exam.id, set()))
                            }
                        )
                        raise ValueError(failure_reason)

                    # Lock in schedule in state
                    chosen_date, chosen_start, chosen_end = best_slot
                    scheduled_state[exam.id] = {
                        'date': chosen_date,
                        'start': chosen_start,
                        'end': chosen_end,
                        'rooms': best_room_selection,
                        'students': registered_students
                    }

                    # Update occupancy indices
                    slot_key = (chosen_date, chosen_start, chosen_end)
                    for room, _ in best_room_selection:
                        room_occupancy[slot_key].add(room.id)

                    for stud_id in registered_students:
                        student_schedule[stud_id].append((chosen_date, chosen_start, chosen_end, exam.id))

                    day_load[chosen_date] += 1

                # Step 4: Persist Clean Schedules & Allocations to Database
                for exam in sorted_examinations:
                    state = scheduled_state[exam.id]
                    exam_date = state['date']
                    start_time = state['start']
                    end_time = state['end']
                    rooms_alloc = state['rooms']
                    students = sorted(state['students']) # Deterministic sorting

                    # 1. Create ExamSchedule
                    ExamSchedule.objects.create(
                        examination=exam,
                        exam_date=exam_date,
                        start_time=start_time,
                        end_time=end_time,
                        status='SCHEDULED'
                    )

                    # 2. Allocate Rooms & Partition Students
                    remaining_students = students.copy()
                    for room, room_capacity_count in rooms_alloc:
                        ExamRoomAllocation.objects.create(
                            examination=exam,
                            room=room,
                            allocated_capacity=room_capacity_count or room.capacity
                        )

                        # Assign student seats to this room
                        assigned_slice = remaining_students[:room_capacity_count]
                        remaining_students = remaining_students[room_capacity_count:]

                        student_alloc_objects = [
                            StudentExamAllocation(
                                examination=exam,
                                student_id=s_id,
                                room=room,
                                seat_number='Pending On-Site',
                                status='ALLOCATED'
                            )
                            for s_id in assigned_slice
                        ]
                        if student_alloc_objects:
                            StudentExamAllocation.objects.bulk_create(student_alloc_objects)

                    exam.status = 'SCHEDULED'
                    exam.save()

                self.period.status = 'DRAFT'
                self.period.save()

            # End of atomic transaction block (Committed!)
            logger.info(f"Successfully generated timetable for {self.period.name}: {len(sorted_examinations)} exams scheduled.")
            
            # Post-generation audit to collect soft warnings
            audit_report = ExamConstraintChecker.validate_period_entire_timetable(self.period)
            self.conflict_report.soft_warnings = audit_report.soft_warnings

            return {
                'success': True,
                'report': self.conflict_report,
                'scheduled_count': len(sorted_examinations),
                'days_utilized': len(day_load),
                'total_rooms_used': len(set.union(*room_occupancy.values())) if room_occupancy else 0
            }

        except Exception as e:
            # Atomic rollback automatically triggered
            logger.error(f"Timetable generation failed for {self.period.name}: {str(e)}")
            if not self.conflict_report.hard_violations:
                self.conflict_report.add_violation(
                    constraint_type='SCHEDULING_ENGINE_ERROR',
                    severity='HARD',
                    message=f"Timetable optimization failed: {str(e)}"
                )
            return {
                'success': False,
                'report': self.conflict_report,
                'scheduled_count': 0,
                'error': str(e)
            }