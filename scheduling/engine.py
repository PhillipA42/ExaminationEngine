from datetime import datetime, timedelta, time
from django.db import transaction
from academics.models import UnitRegistration
from locations.models import Room
from .models import ExaminationPeriod, Examination, ExamSchedule, ExamRoomAllocation, StudentExamAllocation

class TimetableSchedulerEngine:
    def __init__(self, period_id, time_slots=None):
        self.period = ExaminationPeriod.objects.get(id=period_id)
        self.examinations = Examination.objects.filter(period=self.period)
        self.rooms = list(Room.objects.filter(status='ACTIVE'))
        
        # Default time slots per day: Morning (09:00 - 12:00) & Afternoon (14:00 - 17:00)
        self.time_slots = time_slots or [
            (time(9, 0), time(12, 0)),
            (time(14, 0), time(17, 0))
        ]

    def _generate_available_dates(self):
        """Generates weekdays (Mon-Fri) within the exam period."""
        dates = []
        current = self.period.start_date
        while current <= self.period.end_date:
            if current.weekday() < 5:  # Monday to Friday
                dates.append(current)
            current += timedelta(days=1)
        return dates

    def generate_timetable(self):
        """
        Executes constraint-based timetable optimization:
        1. Avoids student exam clashes (Hard Constraint).
        2. Avoids room double-booking (Hard Constraint).
        3. Allocates rooms to meet total student registration count (Hard Constraint).
        """
        available_dates = self._generate_available_dates()
        scheduled_slots = [] # Stores: {'date', 'start_time', 'end_time', 'unit_id', 'student_ids'}
        room_schedules = []   # Stores: {'date', 'start_time', 'end_time', 'room_id'}

        with transaction.atomic():
            # Clear existing draft schedules for this period
            ExamSchedule.objects.filter(examination__period=self.period).delete()
            ExamRoomAllocation.objects.filter(examination__period=self.period).delete()
            StudentExamAllocation.objects.filter(examination__period=self.period).delete()

            for exam in self.examinations:
                # Find all students registered for this exam's unit
                registered_students = list(
                    UnitRegistration.objects.filter(
                        unit=exam.unit,
                        registration_status='REGISTERED'
                    ).values_list('student_id', flat=True)
                )

                total_students = len(registered_students)
                assigned = False

                for exam_date in available_dates:
                    if assigned:
                        break

                    for start_time, end_time in self.time_slots:
                        # Check Hard Constraint 1: Student Clash
                        clash_found = False
                        for slot in scheduled_slots:
                            if slot['date'] == exam_date and slot['start_time'] == start_time:
                                # Intersection of students in same slot
                                if set(registered_students).intersection(set(slot['student_ids'])):
                                    clash_found = True
                                    break

                        if clash_found:
                            continue

                        # Check Hard Constraint 2: Room Capacity Allocation
                        available_rooms_for_slot = []
                        capacity_accumulated = 0

                        for room in self.rooms:
                            # Verify room isn't booked in this date/time slot
                            room_busy = any(
                                r['date'] == exam_date and 
                                r['start_time'] == start_time and 
                                r['room_id'] == room.id
                                for r in room_schedules
                            )
                            if not room_busy:
                                available_rooms_for_slot.append(room)
                                capacity_accumulated += room.capacity
                                if capacity_accumulated >= total_students:
                                    break

                        # If enough room capacity exists, lock in schedule
                        if capacity_accumulated >= total_students or (not registered_students and available_rooms_for_slot):
                            # Save Exam Schedule
                            schedule = ExamSchedule.objects.create(
                                examination=exam,
                                exam_date=exam_date,
                                start_time=start_time,
                                end_time=end_time,
                                status='SCHEDULED'
                            )

                            # Allocate Rooms & Assign Students to Rooms
                            remaining_students = registered_students.copy()

                            for room in available_rooms_for_slot:
                                room_student_count = min(len(remaining_students), room.capacity)
                                
                                if room_student_count > 0 or not registered_students:
                                    ExamRoomAllocation.objects.create(
                                        examination=exam,
                                        room=room,
                                        allocated_capacity=room_student_count or room.capacity
                                    )

                                    room_schedules.append({
                                        'date': exam_date,
                                        'start_time': start_time,
                                        'end_time': end_time,
                                        'room_id': room.id
                                    })

                                    # Assign student room locations
                                    room_assigned_students = remaining_students[:room_student_count]
                                    remaining_students = remaining_students[room_student_count:]

                                    for stud_id in room_assigned_students:
                                        StudentExamAllocation.objects.create(
                                            examination=exam,
                                            student_id=stud_id,
                                            room=room,
                                            seat_number='Pending On-Site'
                                        )

                            scheduled_slots.append({
                                'date': exam_date,
                                'start_time': start_time,
                                'end_time': end_time,
                                'unit_id': exam.unit_id,
                                'student_ids': registered_students
                            })

                            exam.status = 'SCHEDULED'
                            exam.save()
                            assigned = True
                            break

            self.period.status = 'DRAFT'
            self.period.save()

        return True