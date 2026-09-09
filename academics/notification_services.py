"""
Milestone 9 — NotificationService
====================================
Multi-channel notification dispatch: In-App (persistent), Email (Django core), SMS (stub).
Provides deduplication via dedup_key to avoid resending the same notification twice.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from academics.models import Notification, NotificationDelivery

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Channel adapters (thin wrappers; swap for real providers without API changes)
# ─────────────────────────────────────────────────────────────────────────────

class _EmailAdapter:
    def send(self, recipient_user, subject: str, body: str) -> bool:
        email = getattr(recipient_user, "email", None)
        if not email:
            logger.warning("NotificationService: user %s has no email address; skipping email.", recipient_user)
            return False
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@examinationengine.ac"),
                recipient_list=[email],
                fail_silently=False,
            )
            return True
        except Exception as exc:
            logger.error("Email dispatch failed for %s: %s", email, exc)
            return False


class _SMSAdapter:
    """Stub SMS adapter. Replace with Twilio / Africa's Talking / etc."""

    def send(self, recipient_user, message: str) -> bool:
        phone = getattr(recipient_user, "phone_number", None)
        if not phone:
            logger.debug("NotificationService: user %s has no phone_number; SMS skipped.", recipient_user)
            return False
        # TODO: integrate real SMS provider
        logger.info("[SMS STUB] To %s: %s", phone, message[:160])
        return True  # Optimistically mark as sent for now


# ─────────────────────────────────────────────────────────────────────────────
# Main service
# ─────────────────────────────────────────────────────────────────────────────

class NotificationService:
    """
    Central notification dispatcher.

    Usage example:
        svc = NotificationService()
        svc.notify(
            recipient=user,
            notification_type='ELIGIBILITY_UPDATE',
            title='Your eligibility has been updated',
            message='You are now ELIGIBLE to sit the upcoming examination.',
            channels=['IN_APP', 'EMAIL'],
        )
    """

    CHANNELS_ALL = ("IN_APP", "EMAIL", "SMS")
    DEFAULT_CHANNELS = ("IN_APP",)

    def __init__(self):
        self._email = _EmailAdapter()
        self._sms = _SMSAdapter()

    # ────────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────────

    def notify(
        self,
        recipient,
        notification_type: str,
        title: str,
        message: str,
        related_link: Optional[str] = None,
        channels: tuple | list = DEFAULT_CHANNELS,
        dedup_context: Optional[str] = None,
    ) -> Optional[Notification]:
        """
        Create and dispatch a notification to the specified channels.

        Args:
            recipient:           AUTH_USER_MODEL instance.
            notification_type:   One of Notification.NOTIFICATION_TYPES codes.
            title:               Short notification heading.
            message:             Full notification body text.
            related_link:        Optional URL for the related resource.
            channels:            Iterable of channel codes: 'IN_APP', 'EMAIL', 'SMS'.
            dedup_context:       Optional extra string that participates in dedup key
                                 generation (e.g. the target object's id).

        Returns:
            The created Notification instance, or None if all channels were deduped.
        """
        # Build dedup key
        dedup_key = self._make_dedup_key(recipient, notification_type, dedup_context)

        # Check if already sent via IN_APP (the primary channel) within dedup scope
        if NotificationDelivery.objects.filter(dedup_key=dedup_key, channel="IN_APP").exists():
            logger.debug("NotificationService: deduped notification %s for user %s", notification_type, recipient)
            return None

        # Create the Notification record
        notification = Notification.objects.create(
            recipient=recipient,
            title=title,
            message=message,
            notification_type=notification_type,
            related_link=related_link or "",
        )

        # Dispatch to each requested channel
        for channel in channels:
            self._dispatch(notification, channel, dedup_key)

        return notification

    def mark_read(self, notification_id: int, user) -> bool:
        """Mark a notification as read. Returns True if updated."""
        updated = Notification.objects.filter(
            id=notification_id,
            recipient=user,
            is_read=False,
        ).update(is_read=True, read_at=timezone.now())
        return updated > 0

    def mark_all_read(self, user) -> int:
        """Mark all unread notifications for a user as read."""
        return Notification.objects.filter(
            recipient=user,
            is_read=False,
        ).update(is_read=True, read_at=timezone.now())

    def get_unread_count(self, user) -> int:
        return Notification.objects.filter(recipient=user, is_read=False).count()

    def get_recent(self, user, limit: int = 20):
        return Notification.objects.filter(recipient=user).order_by("-created_at")[:limit]

    # ────────────────────────────────────────────────────────────────────
    # Convenience factory methods for common notification types
    # ────────────────────────────────────────────────────────────────────

    def notify_eligibility_update(self, student_user, period_name: str, new_status: str, channels=("IN_APP", "EMAIL")):
        status_label = {
            "ELIGIBLE": "✅ Eligible",
            "NOT_ELIGIBLE": "❌ Not Eligible",
            "CONDITIONALLY_ELIGIBLE": "⚠️ Conditionally Eligible",
            "PENDING_CLEARANCE": "🕐 Pending Clearance",
        }.get(new_status, new_status)

        return self.notify(
            recipient=student_user,
            notification_type="ELIGIBILITY_UPDATE",
            title=f"Examination Eligibility Update — {period_name}",
            message=(
                f"Your eligibility status for {period_name} has been updated to: {status_label}.\n"
                "Please log in to the Student Portal for full details."
            ),
            related_link="/student/exam-pass/",
            channels=channels,
            dedup_context=f"{period_name}:{new_status}",
        )

    def notify_timetable_published(self, student_user, period_name: str, channels=("IN_APP",)):
        return self.notify(
            recipient=student_user,
            notification_type="TIMETABLE_PUBLISHED",
            title=f"Examination Timetable Published — {period_name}",
            message=(
                f"The examination timetable for {period_name} is now available. "
                "Log in to the Student Portal to view your personalised schedule."
            ),
            related_link="/student/timetable/",
            channels=channels,
            dedup_context=period_name,
        )

    def notify_results_published(self, student_user, unit_code: str, channels=("IN_APP",)):
        return self.notify(
            recipient=student_user,
            notification_type="RESULTS_PUBLISHED",
            title=f"Examination Results Released — {unit_code}",
            message=(
                f"Results for {unit_code} have been published. "
                "Log in to the Student Portal to view your grade."
            ),
            related_link="/student/results/",
            channels=channels,
            dedup_context=unit_code,
        )

    def notify_duty_assigned(self, lecturer_user, unit_code: str, exam_date: str, room_name: str, channels=("IN_APP",)):
        return self.notify(
            recipient=lecturer_user,
            notification_type="DUTY_ASSIGNED",
            title=f"Invigilation Duty Assigned — {unit_code}",
            message=(
                f"You have been assigned to invigilate {unit_code} on {exam_date} in Room {room_name}. "
                "Log in to the Invigilator Portal for further details."
            ),
            related_link="/invigilator/dashboard/",
            channels=channels,
            dedup_context=f"{unit_code}:{exam_date}:{room_name}",
        )

    def notify_results_submitted(self, officer_user, unit_code: str, submission_id: int, channels=("IN_APP",)):
        return self.notify(
            recipient=officer_user,
            notification_type="RESULTS_SUBMITTED",
            title=f"Results Submitted for Review — {unit_code}",
            message=f"Marks for {unit_code} (Submission #{submission_id}) have been submitted and are awaiting review.",
            related_link=f"/results/officer/dashboard/",
            channels=channels,
            dedup_context=f"sub:{submission_id}",
        )

    def notify_results_rejected(self, lecturer_user, unit_code: str, submission_id: int, reason: str, channels=("IN_APP", "EMAIL")):
        return self.notify(
            recipient=lecturer_user,
            notification_type="RESULTS_REJECTED",
            title=f"Results Returned for Correction — {unit_code}",
            message=(
                f"Your mark submission for {unit_code} (#{submission_id}) has been returned.\n"
                f"Reason: {reason}"
            ),
            related_link="/results/lecturer/dashboard/",
            channels=channels,
            dedup_context=f"sub:{submission_id}:rejected",
        )

    # ────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ────────────────────────────────────────────────────────────────────

    def _dispatch(self, notification: Notification, channel: str, dedup_key: str):
        """Dispatch a single notification to a single channel and record the delivery."""
        success = False
        error_message = ""
        channel_dedup = f"{dedup_key}:{channel}"

        if NotificationDelivery.objects.filter(dedup_key=channel_dedup).exists():
            logger.debug("NotificationService: channel-level dedup for %s via %s", notification.title, channel)
            return

        try:
            if channel == "IN_APP":
                # Already persisted; IN_APP == DB record existence
                success = True
            elif channel == "EMAIL":
                success = self._email.send(
                    notification.recipient,
                    subject=notification.title,
                    body=notification.message,
                )
            elif channel == "SMS":
                success = self._sms.send(notification.recipient, notification.message)
            else:
                logger.warning("NotificationService: unknown channel '%s'", channel)
                return
        except Exception as exc:
            error_message = str(exc)
            logger.error("NotificationService: dispatch error on channel %s: %s", channel, exc)

        NotificationDelivery.objects.create(
            notification=notification,
            channel=channel,
            status="SENT" if success else "FAILED",
            error_message=error_message if not success else "",
            dedup_key=channel_dedup,
        )

    @staticmethod
    def _make_dedup_key(recipient, notification_type: str, context: Optional[str]) -> str:
        raw = f"uid:{recipient.pk}:{notification_type}:{context or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()[:64]
