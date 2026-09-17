import hashlib
from django.db import transaction
from django.utils import timezone
from invigilators.models import InvigilatorDuty, ExamAttendance
from scheduling.models import StudentExamAllocation
from .models import MalpracticeCase, MalpracticeEvidence, MalpracticeAudit

class MalpracticeError(Exception): pass

class MalpracticeService:
    ALLOWED_EXTENSIONS = {'.jpg','.jpeg','.png','.pdf','.txt'}
    MAX_EVIDENCE_SIZE = 10 * 1024 * 1024

    @staticmethod
    def _officer(user):
        roles = set(user.user_roles.filter(status='ACTIVE').values_list('role__name', flat=True)) if hasattr(user,'user_roles') else set()
        return user.is_superuser or bool(roles & {'EXAM_OFFICER','ADMIN'})

    @classmethod
    def report(cls, duty, lecturer, student, incident_type, description, severity='MEDIUM'):
        if duty.lecturer_id != lecturer.id: raise MalpracticeError('Only the assigned invigilator may report this session.')
        if not StudentExamAllocation.objects.filter(examination=duty.examination, room=duty.room, student=student).exists():
            raise MalpracticeError('Student is not allocated to this examination room.')
        if not incident_type or not description: raise MalpracticeError('Incident type and description are required.')
        with transaction.atomic():
            case = MalpracticeCase.objects.create(case_number=f'MAL-{timezone.now():%Y%m%d%H%M%S%f}', examination=duty.examination, student=student, room=duty.room, reported_by=lecturer, incident_type=incident_type, description=description, severity=severity, status='REPORTED')
            MalpracticeAudit.objects.create(case=case, actor=lecturer, action='REPORTED', details={'attendance_id': ExamAttendance.objects.filter(examination=duty.examination,student=student).values_list('id',flat=True).first()})
        return case

    @classmethod
    def add_evidence(cls, case, lecturer, upload, description=''):
        import os
        ext = os.path.splitext(upload.name)[1].lower()
        if ext not in cls.ALLOWED_EXTENSIONS or upload.size > cls.MAX_EVIDENCE_SIZE: raise MalpracticeError('Evidence must be JPG, PNG, PDF or TXT and at most 10 MB.')
        digest = hashlib.sha256()
        for chunk in upload.chunks(): digest.update(chunk)
        upload.seek(0)
        with transaction.atomic():
            evidence = MalpracticeEvidence.objects.create(case=case, file=upload, description=description, uploaded_by=lecturer, checksum=digest.hexdigest())
            MalpracticeAudit.objects.create(case=case, actor=lecturer, action='EVIDENCE_UPLOADED', details={'evidence_id':evidence.id,'checksum':evidence.checksum})
        return evidence

    @classmethod
    def transition(cls, case, user, status, determination=''):
        if not cls._officer(user): raise MalpracticeError('Only an examination officer may review or determine a case.')
        allowed = {'REPORTED': {'UNDER_INVESTIGATION','DISMISS'}, 'UNDER_INVESTIGATION': {'DISMISS','SANCTIONED'}}
        if status not in allowed.get(case.status, set()): raise MalpracticeError('Invalid case status transition.')
        lecturer = getattr(user,'lecturer_profile',None)
        case.status=status; case.reviewed_by=lecturer; case.reviewed_at=timezone.now(); case.determination=determination; case.save(update_fields=['status','reviewed_by','reviewed_at','determination','updated_at'])
        MalpracticeAudit.objects.create(case=case, actor=lecturer, action='STATUS_CHANGED', details={'status':status,'determination':determination})
        return case
