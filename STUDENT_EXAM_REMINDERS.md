# Student examination reminders

The `send_exam_reminders` Django management command creates an in-app notification for each active student exam allocation at the scheduled exam start time minus 24 hours and minus 1 hour. Each reminder includes unit code/name, venue room and building, local Nairobi date/time, and a student-authorized link to the venue navigation page. Repeated invocations are deduplicated per student, allocation, exam, and reminder lead time.

## Run manually

From the project root, run:

```powershell
.\venv\Scripts\python.exe manage.py send_exam_reminders --window-minutes 5
```

Run it every minute so both reminder times are handled promptly. The five-minute window is a short catch-up allowance if a scheduled run starts late; reminders outside that window are not sent.

## Windows Task Scheduler

Create a task with:

- **Program/script:** the project's `venv\Scripts\python.exe`
- **Arguments:** `manage.py send_exam_reminders --window-minutes 5`
- **Start in:** the project root (the directory containing `manage.py`)
- **Trigger:** repeat every 1 minute indefinitely
- **Action:** start the program whether or not the user is logged in, using the same Windows account/environment that can access the application's database and settings

For Linux deployments, schedule the same command with cron or the deployment platform's scheduler to run every minute.

Reminders currently use the authenticated Student Portal inbox (in-app channel). External email/SMS delivery depends on configuring a real provider; the development email backend does not deliver to student mailboxes.
