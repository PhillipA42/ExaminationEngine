from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('academics', '0006_notification_event_types')]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='notification_type',
            field=models.CharField(max_length=50, choices=[
                ('TIMETABLE_PUBLISHED', 'Timetable Published'),
                ('TIMETABLE_CHANGE', 'Timetable Schedule Change'),
                ('EXAM_REMINDER', 'Upcoming Examination Reminder'),
                ('ELIGIBILITY_UPDATE', 'Examination Eligibility Update'),
                ('EXAM_PASS_READY', 'Examination Pass Available'),
                ('DUTY_REMINDER', 'Upcoming Invigilation Duty Reminder'),
                ('DUTY_ASSIGNED', 'New Invigilation Duty Assigned'),
                ('RESULTS_PUBLISHED', 'Examination Results Published'),
                ('RESULTS_SUBMITTED', 'Results Awaiting Review'),
                ('RESULTS_REJECTED', 'Results Rejected / Returned'),
                ('MALPRACTICE_FLAGGED', 'Malpractice Case Update'),
                ('ATTENDANCE_RECORDED', 'Examination Attendance Recorded'),
                ('MARKS_ENTERED', 'Marks Entered (Pending Approval)'),
                ('ADMIN_ALERT', 'Administrative Alert'),
            ]),
        ),
    ]
