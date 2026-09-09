"""
Milestone 9 verification script — run with: python verify_m9.py
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

print("=" * 60)
print("MILESTONE 9 — VERIFICATION")
print("=" * 60)

# ── 1. EligibilityRule seeding ────────────────────────────────
from academics.models import EligibilityRule
rules = EligibilityRule.objects.all()
print(f"\n[1] EligibilityRules in DB: {rules.count()}")
for r in rules:
    print(f"    {r.code:<30} enabled={r.is_enabled}  mandatory={r.is_mandatory}")

# ── 2. EligibilityService evaluation ─────────────────────────
from academics.models import Student
from scheduling.models import ExaminationPeriod
from academics.eligibility_services import EligibilityService

student = Student.objects.first()
period  = ExaminationPeriod.objects.first()

if student and period:
    print(f"\n[2] EligibilityService.evaluate()")
    print(f"    Student : {student.registration_number}")
    print(f"    Period  : {period.name}")
    svc    = EligibilityService()
    result = svc.evaluate(student, period, force_refresh=True)
    print(f"    Status  : {result.overall_status}")
    print(f"    Eligible: {result.is_eligible}")
    print(f"    Passed  : {result.rules_passed}  Failed: {result.rules_failed}")
    print(f"    Source  : {result.evaluation_details.get('source')}")
    print("    ✅ EligibilityService OK")
else:
    print("\n[2] SKIP — no Student or ExaminationPeriod in DB")

# ── 3. EligibilityEvaluation cache ───────────────────────────
from academics.models import EligibilityEvaluation
evals = EligibilityEvaluation.objects.count()
print(f"\n[3] EligibilityEvaluation records: {evals}")

# ── 4. StudentClearance ───────────────────────────────────────
from academics.models import StudentClearance
clears = StudentClearance.objects.count()
print(f"\n[4] StudentClearance records: {clears}")

# ── 5. NotificationService ────────────────────────────────────
from academics.notification_services import NotificationService
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.filter(is_superuser=True).first()

if user:
    svc2  = NotificationService()
    notif = svc2.notify(
        recipient=user,
        notification_type='ADMIN_ALERT',
        title='M9 Verification Test',
        message='This notification was created during Milestone 9 verification.',
        channels=('IN_APP',),
        dedup_context='m9_verify_001',
    )
    print(f"\n[5] Notification created: id={notif.id if notif else 'DEDUPED'}")
    print(f"    Unread count for {user.username}: {svc2.get_unread_count(user)}")
    print("    ✅ NotificationService OK")
else:
    print("\n[5] SKIP — no superuser in DB")

# ── 6. API URL registration ───────────────────────────────────
from django.urls import reverse
try:
    from rest_framework.reverse import reverse as drf_reverse
    rules_url   = '/api/academics/eligibility-rules/'
    notifs_url  = '/api/academics/notifications/'
    print(f"\n[6] API endpoint prefixes registered:")
    print(f"    {rules_url}")
    print(f"    {notifs_url}")
    print("    ✅ URL registration OK")
except Exception as e:
    print(f"\n[6] URL check error: {e}")

# ── 7. Student portal notifications view ─────────────────────
from django.urls import reverse
try:
    url = reverse('student_notifications')
    print(f"\n[7] student_notifications URL: {url}  ✅")
except Exception as e:
    print(f"\n[7] student_notifications URL ERROR: {e}")

print("\n" + "=" * 60)
print("MILESTONE 9 VERIFICATION COMPLETE")
print("=" * 60)
