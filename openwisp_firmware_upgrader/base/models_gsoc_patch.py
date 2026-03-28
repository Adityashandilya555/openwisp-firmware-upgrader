"""
GSoC 2026 Prototype – Model field additions.

This module documents (and provides mixins for) the new fields added to
AbstractBatchUpgradeOperation and AbstractUpgradeOperation.

In the full implementation these would live directly inside
openwisp_firmware_upgrader/base/models.py, but for the prototype we keep
them in a separate file so the diff is easy to review.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# ─────────────────────────────────────────────────────────────────────────────
# Mixin: adds the `persistent` and `scheduled_at` fields to
#        AbstractBatchUpgradeOperation
# ─────────────────────────────────────────────────────────────────────────────


class PersistentScheduledBatchMixin(models.Model):
    """
    Mixin that must be placed *before* AbstractBatchUpgradeOperation in MRO.

    Fields added
    ~~~~~~~~~~~~
    persistent : bool
        When True, UpgradeOperations that fail because the device is offline
        will be automatically retried according to the exponential back-off
        strategy.  The field is set at creation time and cannot be changed
        afterwards (enforced in ``clean()``).

    scheduled_at : datetime | None
        If set, the batch is not started immediately; instead a Celery Beat
        periodic task polls every minute and starts batches whose
        ``scheduled_at`` has elapsed.  Stored in UTC.

    STATUS_CHOICES extension
    ~~~~~~~~~~~~~~~~~~~~~~~~
    The ``scheduled`` status is added on top of the parent's choices so that
    the batch list can show a clock icon for pending batches.
    """

    # ------------------------------------------------------------------
    # New fields
    # ------------------------------------------------------------------
    persistent = models.BooleanField(
        default=True,
        verbose_name=_("persistent"),
        help_text=_(
            "When enabled, offline devices will be retried automatically "
            "using exponential back-off. Immutable after creation."
        ),
    )
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        verbose_name=_("scheduled at"),
        help_text=_(
            "UTC datetime at which this batch should start. "
            "Leave blank for immediate execution."
        ),
    )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    _SCHEDULE_MIN_DELAY_MINUTES = 10
    _SCHEDULE_MAX_HORIZON_MONTHS = 6

    def clean(self):
        super().clean()
        self._validate_scheduled_at()
        self._validate_persistent_immutable()

    def _validate_scheduled_at(self):
        if not self.scheduled_at:
            return
        now = timezone.now()
        min_allowed = now + timedelta(minutes=self._SCHEDULE_MIN_DELAY_MINUTES)
        max_allowed = now + timedelta(days=self._SCHEDULE_MAX_HORIZON_MONTHS * 30)
        if self.scheduled_at <= now:
            raise ValidationError(
                {"scheduled_at": _("Scheduled time must be in the future.")}
            )
        if self.scheduled_at < min_allowed:
            raise ValidationError(
                {
                    "scheduled_at": _(
                        f"Scheduled time must be at least "
                        f"{self._SCHEDULE_MIN_DELAY_MINUTES} minutes from now."
                    )
                }
            )
        if self.scheduled_at > max_allowed:
            raise ValidationError(
                {
                    "scheduled_at": _(
                        f"Scheduled time cannot be more than "
                        f"{self._SCHEDULE_MAX_HORIZON_MONTHS} months in the future."
                    )
                }
            )

    def _validate_persistent_immutable(self):
        """Prevent changing `persistent` after the batch has been saved."""
        if not self.pk:
            return  # new object – any value is fine
        try:
            original = self.__class__.objects.get(pk=self.pk)
        except self.__class__.DoesNotExist:
            return
        if original.persistent != self.persistent:
            raise ValidationError(
                {
                    "persistent": _(
                        "The persistent flag cannot be changed after the batch "
                        "has been created."
                    )
                }
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @property
    def is_scheduled(self):
        return self.scheduled_at is not None and self.status == "scheduled"

    @property
    def scheduled_at_local(self):
        """Return ``scheduled_at`` converted to the server's local timezone."""
        if not self.scheduled_at:
            return None
        return timezone.localtime(self.scheduled_at)

    class Meta:
        abstract = True


# ─────────────────────────────────────────────────────────────────────────────
# Mixin: adds retry tracking fields to AbstractUpgradeOperation
# ─────────────────────────────────────────────────────────────────────────────


class RetryTrackingMixin(models.Model):
    """
    Mixin that must be placed *before* AbstractUpgradeOperation in MRO.

    Fields added
    ~~~~~~~~~~~~
    retry_count : int
        Number of times the upgrade has been retried due to the device
        being offline.

    next_retry_at : datetime | None
        The UTC time at which the next retry is scheduled.  Set by the
        ``schedule_retry()`` helper after each failed attempt.

    waiting_for_device : bool
        True when the operation is paused waiting for the device to
        come online (status == "waiting").

    Status extension
    ~~~~~~~~~~~~~~~~
    The ``waiting`` status is added so the batch detail page can show a
    "Waiting for device" badge with a distinct colour, clearly separated
    from permanent failures.
    """

    retry_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_("retry count"),
    )
    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        verbose_name=_("next retry at"),
    )
    waiting_for_device = models.BooleanField(
        default=False,
        verbose_name=_("waiting for device"),
    )

    def schedule_retry(self, batch_persistent, delay_seconds):
        """
        Mark this operation as 'waiting' and schedule the next retry.

        Parameters
        ----------
        batch_persistent : bool
            Taken from the parent BatchUpgradeOperation.persistent field.
        delay_seconds : int
            Number of seconds to wait before the next retry attempt.
            Computed by the caller using the exponential back-off helper.
        """
        if not batch_persistent:
            # Non-persistent batches do not retry.
            self.status = "failed"
            self.save(update_fields=["status"])
            return

        self.retry_count += 1
        self.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
        self.waiting_for_device = True
        self.status = "waiting"
        self.save(
            update_fields=[
                "retry_count",
                "next_retry_at",
                "waiting_for_device",
                "status",
            ]
        )

    @property
    def next_retry_at_display(self):
        """Return a human-friendly string like 'Pending retry at 14:35'."""
        if not self.next_retry_at:
            return _("–")
        local_dt = timezone.localtime(self.next_retry_at)
        return _("Pending retry at %(time)s") % {"time": local_dt.strftime("%H:%M")}

    class Meta:
        abstract = True
