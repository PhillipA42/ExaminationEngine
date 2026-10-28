from django.db import migrations


def clear_tab_sessions(apps, schema_editor):
    BrowserTabSession = apps.get_model("authentication", "BrowserTabSession")
    BrowserTabSession.objects.using(schema_editor.connection.alias).all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0004_remove_browsertabsession_csrf_secret"),
    ]

    operations = [
        migrations.RunPython(clear_tab_sessions, migrations.RunPython.noop),
    ]
