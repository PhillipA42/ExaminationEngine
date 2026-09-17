# System verification (Module 7)

Module 7 is a read-only assurance layer over Modules 1--6.  It persists each run and its individual checks in `SystemVerificationReport` and `VerificationCheck`; it never repairs, deletes, reallocates, or resolves source records.

Run it from Windows PowerShell:

```powershell
python manage.py migrate
python manage.py verify_system
python manage.py reconcile_exams
python manage.py test
```

Checks are grouped into database, academic structure, locations, authentication/identity, scheduling, room and student allocation, invigilation, attendance, booklets, and malpractice relationships.  Timetable validation delegates to `scheduling.services.ExamConstraintChecker`, the established M2 constraint engine.  Reconciliation remains the separate M6 `reconcile_exams` command.

`PASS` means the required invariant holds; `FAIL` means it does not; `WARNING` calls out an intentionally non-fatal condition (such as an administrative-only account); and `SKIPPED` means a check cannot apply.  A report is `FAILED` for any failure, `PASSED_WITH_WARNINGS` for warnings only, and otherwise `PASSED`.

For a failed check, use its category, code, expected/actual values and JSON details in Django admin to locate the affected workflow. Correct it through the existing authorized workflow, then run the command again.
