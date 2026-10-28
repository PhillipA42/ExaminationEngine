from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0003_browsertabsession"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="browsertabsession",
            name="csrf_secret",
        ),
    ]
