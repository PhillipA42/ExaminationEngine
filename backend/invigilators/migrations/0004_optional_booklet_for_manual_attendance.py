from django.db import migrations, models


def clear_absent_booklet_placeholders(apps, schema_editor):
    attendance = apps.get_model('invigilators', 'ExamAttendance')
    attendance.objects.filter(is_present=False).update(booklet_serial_number=None)


class Migration(migrations.Migration):
    dependencies = [('invigilators', '0003_align_index_names')]

    operations = [
        migrations.RunPython(clear_absent_booklet_placeholders, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='examattendance',
            name='booklet_serial_number',
            field=models.CharField(
                blank=True,
                null=True,
                unique=True,
                max_length=100,
                help_text='Physical booklet number recorded by the lecturer for a present student',
            ),
        ),
    ]
