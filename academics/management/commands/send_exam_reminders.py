from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone

from academics.models import Notification
from academics.notification_services import NotificationService
from scheduling.models import StudentExamAllocation


class Command(BaseCommand):
    help = (
        'Create personalized in-app reminders for published student exams '
        'one day and one hour before they start. Run this command every minute.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--window-minutes',
            type=int,
            default=5,
            help='How long after each reminder time the command may still deliver it (default: 5).',
        )

    def handle(self, *args, **options):
        window_minutes = options['window_minutes']
        if window_minutes < 1:
            raise CommandError('--window-minutes must be at least 1.')

        now = timezone.localtime()
        window = timedelta(minutes=window_minutes)
        today = now.date()
        last_candidate_date = (now + timedelta(days=1)).date()
        allocations = StudentExamAllocation.objects.filter(
            examination__period__status='PUBLISHED',
            examination__schedule__status='PUBLISHED',
            examination__schedule__exam_date__range=(today, last_candidate_date),
            student__status='ACTIVE',
        ).select_related(
            'student__user',
            'examination__unit',
            'examination__schedule',
            'room__building__campus',
        ).order_by('examination__schedule__exam_date', 'examination__schedule__start_time', 'pk')

        service = NotificationService()
        created_count = 0
        deduplicated_count = 0

        for allocation in allocations.iterator():
            schedule = allocation.examination.schedule
            exam_start = timezone.make_aware(
                datetime.combine(schedule.exam_date, schedule.start_time),
                timezone.get_current_timezone(),
            )
            reminders = (
                ('1_day', timedelta(days=1), 'in 1 day'),
                ('1_hour', timedelta(hours=1), 'in 1 hour'),
            )

            for reminder_key, lead_time, lead_label in reminders:
                reminder_at = exam_start - lead_time
                if not (reminder_at <= now < reminder_at + window):
                    continue

                exam = allocation.examination
                unit = exam.unit
                room = allocation.room
                building = room.building
                navigation_path = reverse(
                    'student_venue_navigation',
                    kwargs={'allocation_id': allocation.pk},
                )
                title = f'Examination reminder: {unit.code} starts {lead_label}'
                message = (
                    f'{unit.code} — {unit.name}\n'
                    f'Venue: {room.name}, {building.name}\n'
                    f'Date: {schedule.exam_date:%A, %d %B %Y}\n'
                    f'Time: {schedule.start_time:%H:%M}–{schedule.end_time:%H:%M}\n'
                    'Open the navigation link to get directions to your examination building.'
                )
                notification = service.notify(
                    recipient=allocation.student.user,
                    notification_type='EXAM_REMINDER',
                    title=title,
                    message=message,
                    related_link=navigation_path,
                    channels=('IN_APP',),
                    dedup_context=(
                        f'exam:{exam.pk}:allocation:{allocation.pk}:reminder:{reminder_key}'
                    ),
                )
                if notification:
                    created_count += 1
                else:
                    deduplicated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Exam reminders created: {created_count}; already sent: {deduplicated_count}.'
        ))
