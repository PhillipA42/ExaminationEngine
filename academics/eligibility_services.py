"""
Milestone 9 — EligibilityService
===================================
Evaluates student examination eligibility by applying configurable EligibilityRules
(financial clearance, attendance, disciplinary status, administrative clearance).
Results are cached in EligibilityEvaluation for performance and auditability.
Active EligibilityOverride records unconditionally supersede computed status.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from django.db import transaction
from django.db.models import Q

from academics.models import (
    Student,
    StudentClearance,
    EligibilityRule,
    EligibilityOverride,
    EligibilityEvaluation,
)


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityResult:
    """Lightweight value object returned by EligibilityService.evaluate()."""

    def __init__(
        self,
        overall_status: str,
        is_eligible: bool,
        rules_passed: int,
        rules_failed: int,
        has_active_override: bool,
        evaluation_details: dict,
    ):
        self.overall_status = overall_status
        self.is_eligible = is_eligible
        self.rules_passed = rules_passed
        self.rules_failed = rules_failed
        self.has_active_override = has_active_override
        self.evaluation_details = evaluation_details

    def as_dict(self) -> dict:
        return {
            "overall_status": self.overall_status,
            "is_eligible": self.is_eligible,
            "rules_passed": self.rules_passed,
            "rules_failed": self.rules_failed,
            "has_active_override": self.has_active_override,
            "evaluation_details": self.evaluation_details,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Core service
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityService:
    """
    Stateless service that evaluates a student's eligibility for an examination period.

    Usage:
        service = EligibilityService()
        result  = service.evaluate(student, examination_period)
        # or to force-refresh cached evaluation:
        result  = service.evaluate(student, examination_period, force_refresh=True)
    """

    # Map EligibilityRule.code → StudentClearance.clearance_type
    _RULE_TO_CLEARANCE = {
        "FEE_CLEARANCE": "FINANCIAL",
        "ADMIN_CLEARANCE": "ADMINISTRATIVE",
        "DISCIPLINARY_CLEARANCE": "DISCIPLINARY",
        "ATTENDANCE_CLEARANCE": "ATTENDANCE",
    }

    def evaluate(
        self,
        student: Student,
        examination_period,
        force_refresh: bool = False,
    ) -> EligibilityResult:
        """
        Evaluate (and cache) the student's eligibility for the given period.
        Returns an EligibilityResult value object.
        """
        # 1. Return cached evaluation unless a forced refresh is requested
        if not force_refresh:
            cached = EligibilityEvaluation.objects.filter(
                student=student,
                examination_period=examination_period,
            ).first()
            if cached:
                return EligibilityResult(
                    overall_status=cached.overall_status,
                    is_eligible=cached.is_eligible,
                    rules_passed=cached.rules_passed,
                    rules_failed=cached.rules_failed,
                    has_active_override=cached.has_active_override,
                    evaluation_details=cached.evaluation_details,
                )

        # 2. Check for an active override (highest priority)
        now = datetime.now(timezone.utc)
        active_override = EligibilityOverride.objects.filter(
            student=student,
            examination_period=examination_period,
            is_active=True,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).order_by("-authorized_at").first()

        if active_override:
            result = self._build_override_result(active_override)
        else:
            result = self._evaluate_rules(student, examination_period)

        # 3. Persist / update cache
        self._cache_evaluation(student, examination_period, result)
        return result

    # ────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ────────────────────────────────────────────────────────────────────

    def _build_override_result(self, override: EligibilityOverride) -> EligibilityResult:
        """Build a result directly from an administrative override."""
        is_eligible = override.new_status in ("ELIGIBLE", "CONDITIONALLY_ELIGIBLE")
        return EligibilityResult(
            overall_status=override.new_status,
            is_eligible=is_eligible,
            rules_passed=0,
            rules_failed=0,
            has_active_override=True,
            evaluation_details={
                "source": "ADMINISTRATIVE_OVERRIDE",
                "override_id": override.id,
                "reason": override.reason,
                "authorized_by": str(override.authorized_by),
                "new_status": override.new_status,
            },
        )

    def _evaluate_rules(self, student: Student, examination_period) -> EligibilityResult:
        """Apply all enabled EligibilityRules against the student's clearance records."""
        enabled_rules = EligibilityRule.objects.filter(is_enabled=True)
        if not enabled_rules.exists():
            # No rules configured → everyone is eligible by default
            return EligibilityResult(
                overall_status="ELIGIBLE",
                is_eligible=True,
                rules_passed=0,
                rules_failed=0,
                has_active_override=False,
                evaluation_details={"source": "NO_RULES_CONFIGURED"},
            )

        academic_year = getattr(examination_period, "academic_year", None)
        semester = getattr(examination_period, "semester", 1)

        # Fetch all clearance records for this student/period in one query
        clearance_map: dict[str, str] = {}
        if academic_year:
            for sc in StudentClearance.objects.filter(
                student=student,
                academic_year=str(academic_year),
                semester=semester,
            ):
                clearance_map[sc.clearance_type] = sc.status

        rule_details: list[dict] = []
        rules_passed = 0
        rules_failed = 0
        mandatory_failed = False

        for rule in enabled_rules:
            clearance_type = self._RULE_TO_CLEARANCE.get(rule.code)
            status = clearance_map.get(clearance_type, "CLEARED") if clearance_type else "CLEARED"

            passed = status in ("CLEARED", "EXEMPTED")

            if passed:
                rules_passed += 1
            else:
                rules_failed += 1
                if rule.is_mandatory:
                    mandatory_failed = True

            rule_details.append({
                "rule_code": rule.code,
                "rule_name": rule.name,
                "is_mandatory": rule.is_mandatory,
                "clearance_status": status,
                "passed": passed,
            })

        # Determine overall status
        if mandatory_failed:
            overall_status = "NOT_ELIGIBLE"
            is_eligible = False
        elif rules_failed > 0:
            overall_status = "CONDITIONALLY_ELIGIBLE"
            is_eligible = True
        elif rules_passed == 0 and enabled_rules.count() == 0:
            overall_status = "ELIGIBLE"
            is_eligible = True
        else:
            overall_status = "ELIGIBLE"
            is_eligible = True

        return EligibilityResult(
            overall_status=overall_status,
            is_eligible=is_eligible,
            rules_passed=rules_passed,
            rules_failed=rules_failed,
            has_active_override=False,
            evaluation_details={
                "source": "RULE_EVALUATION",
                "academic_year": str(academic_year),
                "semester": semester,
                "rules": rule_details,
            },
        )

    @staticmethod
    def _cache_evaluation(student: Student, examination_period, result: EligibilityResult):
        """Persist / update the EligibilityEvaluation cache record."""
        with transaction.atomic():
            EligibilityEvaluation.objects.update_or_create(
                student=student,
                examination_period=examination_period,
                defaults={
                    "overall_status": result.overall_status,
                    "is_eligible": result.is_eligible,
                    "rules_passed": result.rules_passed,
                    "rules_failed": result.rules_failed,
                    "has_active_override": result.has_active_override,
                    "evaluation_details": result.evaluation_details,
                },
            )

    # ────────────────────────────────────────────────────────────────────
    # Bulk helpers
    # ────────────────────────────────────────────────────────────────────

    def bulk_evaluate(self, students, examination_period, force_refresh: bool = False) -> dict:
        """
        Evaluate multiple students for the same period.
        Returns a dict keyed by student.id → EligibilityResult.
        """
        return {
            student.id: self.evaluate(student, examination_period, force_refresh=force_refresh)
            for student in students
        }

    def get_ineligible_students(self, examination_period) -> list:
        """Return a list of students flagged NOT_ELIGIBLE for the given period."""
        ineligible = EligibilityEvaluation.objects.filter(
            examination_period=examination_period,
            is_eligible=False,
        ).select_related("student", "student__user")
        return [ev.student for ev in ineligible]

    def clear_cache(self, student: Student, examination_period):
        """Remove a cached evaluation so it will be recomputed on next call."""
        EligibilityEvaluation.objects.filter(
            student=student,
            examination_period=examination_period,
        ).delete()
