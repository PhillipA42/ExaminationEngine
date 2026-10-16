from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import (
    ExaminationPeriod, Examination, ExamSchedule,
    ExamRoomAllocation, StudentExamAllocation, ExamTimeSlot
)
from .serializers import (
    ExaminationPeriodSerializer, ExaminationSerializer, ExamScheduleSerializer,
    ExamRoomAllocationSerializer, StudentExamAllocationSerializer, ExamTimeSlotSerializer
)
from .engine import TimetableSchedulerEngine
from .services import ExamConstraintChecker


class IsExamOfficerOrAdmin(permissions.BasePermission):
    """
    Permission check: Examination Officers, Timetablers, and Admins have full management access.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser or getattr(request.user, 'is_exam_officer', False):
            return True
        user_roles = set(request.user.user_roles.filter(status='ACTIVE').values_list('role__name', flat=True)) if hasattr(request.user, 'user_roles') else set()
        return bool(user_roles & {'EXAM_OFFICER', 'ADMIN'})


class ExaminationPeriodViewSet(viewsets.ModelViewSet):
    queryset = ExaminationPeriod.objects.all().order_by('-created_at')
    serializer_class = ExaminationPeriodSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'master_timetable']:
            return [permissions.IsAuthenticated()]
        return [IsExamOfficerOrAdmin()]

    @action(detail=True, methods=['post'], url_path='generate-timetable')
    def generate_timetable(self, request, pk=None):
        """
        API Endpoint to trigger constraint-satisfaction timetable generation engine.
        Guarantees zero hard-constraint violations with complete atomic rollback on failure.
        """
        period = self.get_object()
        scheduler = TimetableSchedulerEngine(period_id=period.id)
        result = scheduler.execute_scheduling()

        if result['success']:
            return Response({
                "message": f"Timetable generated successfully for {period.name}.",
                "scheduled_count": result.get('scheduled_count', 0),
                "days_utilized": result.get('days_utilized', 0),
                "total_rooms_used": result.get('total_rooms_used', 0),
                "report": result['report'].to_dict()
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Failed to generate timetable due to unsatisfied hard constraints.",
                "report": result['report'].to_dict()
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='validate-timetable')
    def validate_timetable(self, request, pk=None):
        """
        Non-destructive constraint audit: Checks all student clashes, room clashes,
        and capacity limits across the entire period.
        """
        period = self.get_object()
        report = ExamConstraintChecker.validate_period_entire_timetable(period)
        return Response(report.to_dict(), status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='publish-timetable')
    def publish_timetable(self, request, pk=None):
        """
        Publication Endpoint: Validates that 0 hard constraints are violated before publishing.
        """
        period = self.get_object()
        success, report = ExamConstraintChecker.publish_timetable(period, request.user)

        if success:
            return Response({
                "message": f"Timetable for {period.name} has been verified and officially published.",
                "report": report.to_dict()
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Cannot publish timetable: Hard constraint conflicts detected.",
                "report": report.to_dict()
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='master-timetable')
    def master_timetable(self, request, pk=None):
        """
        Returns full structured master timetable for the period.
        Supports filtering by school_id, department_id, date, room_id.
        """
        period = self.get_object()
        schedules = ExamSchedule.objects.filter(examination__period=period).select_related(
            'examination__unit__course__department__school',
            'examination__period'
        ).prefetch_related('examination__room_allocations__room')

        # Filters
        date_param = request.query_params.get('date')
        if date_param:
            schedules = schedules.filter(exam_date=date_param)

        dept_id = request.query_params.get('department_id')
        if dept_id:
            schedules = schedules.filter(examination__unit__course__department_id=dept_id)

        school_id = request.query_params.get('school_id')
        if school_id:
            schedules = schedules.filter(examination__unit__course__department__school_id=school_id)

        items = []
        for s in schedules:
            exam = s.examination
            rooms = [
                {
                    'room_id': ra.room.id,
                    'room_name': ra.room.name,
                    'allocated_capacity': ra.allocated_capacity
                }
                for ra in exam.room_allocations.all()
            ]
            items.append({
                'examination_id': exam.id,
                'unit_code': exam.unit.code,
                'unit_name': exam.unit.name,
                'course': exam.unit.course.name,
                'department': exam.unit.course.department.name,
                'school': exam.unit.course.department.school.name,
                'exam_date': str(s.exam_date),
                'start_time': str(s.start_time),
                'end_time': str(s.end_time),
                'status': s.status,
                'rooms': rooms,
                'total_allocated_students': exam.allocated_students_count,
                'total_registered_students': exam.registered_students_count
            })

        return Response({
            'period_id': period.id,
            'period_name': period.name,
            'academic_year': period.academic_year,
            'semester': period.semester,
            'is_published': period.is_published,
            'schedules': items
        })

    @action(detail=True, methods=['get', 'post'], url_path='time-slots')
    def time_slots(self, request, pk=None):
        period = self.get_object()
        if request.method == 'GET':
            slots = period.time_slots.all()
            serializer = ExamTimeSlotSerializer(slots, many=True)
            return Response(serializer.data)
        elif request.method == 'POST':
            serializer = ExamTimeSlotSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save(period=period)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ExaminationViewSet(viewsets.ModelViewSet):
    queryset = Examination.objects.all().select_related('unit', 'period')
    serializer_class = ExaminationSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'registered_students']:
            return [permissions.IsAuthenticated()]
        return [IsExamOfficerOrAdmin()]

    @action(detail=True, methods=['get'], url_path='registered-students')
    def registered_students(self, request, pk=None):
        examination = self.get_object()
        from academics.models import UnitRegistration
        regs = UnitRegistration.objects.filter(
            unit=examination.unit,
            academic_year=examination.period.academic_year,
            semester=examination.period.semester,
            registration_status='REGISTERED'
        ).select_related('student__user')

        students_data = [
            {
                'student_id': r.student.id,
                'registration_number': r.student.registration_number,
                'full_name': r.student.user.get_full_name() or r.student.user.username,
                'email': r.student.user.email
            }
            for r in regs
        ]
        return Response({
            'examination_id': examination.id,
            'unit_code': examination.unit.code,
            'registered_count': len(students_data),
            'students': students_data
        })


class ExamScheduleViewSet(viewsets.ModelViewSet):
    queryset = ExamSchedule.objects.all().select_related('examination__unit', 'examination__period')
    serializer_class = ExamScheduleSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.IsAuthenticated()]
        return [IsExamOfficerOrAdmin()]


class ExamRoomAllocationViewSet(viewsets.ModelViewSet):
    queryset = ExamRoomAllocation.objects.all().select_related('examination', 'room')
    serializer_class = ExamRoomAllocationSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.IsAuthenticated()]
        return [IsExamOfficerOrAdmin()]


class StudentTimetableViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Personalized Student Timetable Endpoint:
    Filters schedule by logged-in student's registered units and allocations.
    """
    serializer_class = StudentExamAllocationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'student_profile'):
            return StudentExamAllocation.objects.filter(
                student=user.student_profile
            ).select_related(
                'examination__unit__course',
                'examination__period',
                'room__building',
                'examination__schedule'
            )
        return StudentExamAllocation.objects.none()