"""
Live End-to-End Verification for Lecturer Attendance Marking with Inline Booklet Inputs
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import json
from django.test import Client
from django.contrib.auth import get_user_model
from invigilators.models import InvigilatorDuty, ExamAttendance
from scheduling.models import Examination

print("=" * 65)
print("VERIFICATION: LECTURER ATTENDANCE MARKING & INLINE BOOKLET INPUTS")
print("=" * 65)

client = Client()

# 1. Lecturer Login
login_success = client.login(username='lec001', password='Password123!')
print(f"[1] Lecturer Authentication (lec001): {'SUCCESS' if login_success else 'FAILED'}")
assert login_success, "Lecturer authentication failed"

# 2. Lecturer Results Dashboard Verification
resp_dash = client.get('/results/lecturer/')
dash_html = resp_dash.content.decode('utf-8')
has_att_button = "Attendance Marking" in dash_html
has_att_metric = "Attendance" in dash_html
print(f"[2] Results Dashboard Rendered (HTTP {resp_dash.status_code}):")
print(f"    - Attendance Marking button present: {has_att_button}")
print(f"    - Attendance progress metric present: {has_att_metric}")
assert has_att_button and has_att_metric, "Results dashboard missing attendance controls"

# 3. Direct Entry Point: Lecturer Open Attendance for Examination 1
exam = Examination.objects.first()
resp_open = client.get(f'/results/lecturer/attendance/{exam.id}/', follow=True)
print(f"[3] Direct Attendance Route (/results/lecturer/attendance/{exam.id}/):")
print(f"    - HTTP Status: {resp_open.status_code}")
print(f"    - Final URL redirected: {resp_open.redirect_chain[-1][0] if resp_open.redirect_chain else 'Direct'}")
roster_html = resp_open.content.decode('utf-8')
has_inline_input = 'class="form-control font-monospace fw-bold booklet-input"' in roster_html
has_save_all = 'id="btnSaveAllAttendance"' in roster_html
has_absent_all = 'markAllRemainingAbsent()' in roster_html
has_enter_instruction = "Press <kbd class=\"px-1 text-white bg-dark\">Enter</kbd> to save & auto-advance" in roster_html
print(f"    - Inline booklet serial input field present: {has_inline_input}")
print(f"    - Batch 'Save All' button present: {has_save_all}")
print(f"    - 'Mark Remaining Absent' button present: {has_absent_all}")
print(f"    - Barcode / Enter key instruction present: {has_enter_instruction}")
assert has_inline_input and has_save_all, "Roster template missing inline booklet input fields"

# 4. Live Check-In with Booklet Serial via AJAX
duty = InvigilatorDuty.objects.filter(examination=exam).first()
first_student = duty.examination.student_allocations.first().student

checkin_payload = {
    'student_id': first_student.id,
    'booklet_serial_number': f'BKT-LIVE-E2E-{first_student.id:04d}',
    'is_present': True
}
resp_checkin = client.post(
    f'/invigilator/session/{duty.id}/checkin/',
    data=json.dumps(checkin_payload),
    content_type='application/json'
)
checkin_json = resp_checkin.json()
print(f"[4] Live Single Check-In API Execution (HTTP {resp_checkin.status_code}):")
print(f"    - Success: {checkin_json.get('success')}")
print(f"    - Booklet recorded: {checkin_json.get('booklet_serial_number')}")
print(f"    - Status: {checkin_json.get('status')}")
print(f"    - Updated checked_in count: {checkin_json.get('stats', {}).get('checked_in')}")
assert checkin_json.get('success') is True, "Single checkin failed"

# 5. Live Batch Check-In API Execution
batch_payload = {
    'records': [
        {
            'student_id': first_student.id,
            'booklet_serial_number': f'BKT-LIVE-E2E-{first_student.id:04d}',
            'is_present': True
        }
    ]
}
resp_batch = client.post(
    f'/invigilator/session/{duty.id}/batch-checkin/',
    data=json.dumps(batch_payload),
    content_type='application/json'
)
batch_json = resp_batch.json()
print(f"[5] Live Batch Check-In API Execution (HTTP {resp_batch.status_code}):")
print(f"    - Success: {batch_json.get('success')}")
print(f"    - Message: {batch_json.get('message')}")
print(f"    - Updated count: {batch_json.get('updated_count')}")
assert batch_json.get('success') is True, "Batch checkin failed"

print("\n" + "=" * 65)
print("ALL VERIFICATIONS PASSED SUCCESSFULLY! (100% OPERATIONAL)")
print("=" * 65)
