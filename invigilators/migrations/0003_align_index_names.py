from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('invigilators', '0002_session_audit_constraints')]
    operations = [
        migrations.RenameIndex(
            model_name='examattendance', old_name='inv_att_exam_room_st_idx',
            new_name='invigilator_examina_8b111d_idx',
        ),
        migrations.RenameIndex(
            model_name='invigilatorduty', old_name='inv_duty_room_exam_idx',
            new_name='invigilator_room_id_1b9e6f_idx',
        ),
    ]
