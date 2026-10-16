from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='role',
            name='name',
            field=models.CharField(
                choices=[
                    ('ADMIN', 'System Administrator'),
                    ('EXAM_OFFICER', 'Examination Officer / Timetabler'),
                    ('ACADEMIC_OFFICER', 'Academic Officer'),
                    ('DEAN', 'Dean of School'),
                    ('COD', 'Chairman of Department'),
                    ('LECTURER', 'Lecturer'),
                    ('STUDENT', 'Student'),
                ],
                max_length=50,
                unique=True,
            ),
        ),
    ]
