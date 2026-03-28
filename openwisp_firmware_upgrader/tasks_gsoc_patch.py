"""
GSoC 2026 Prototype — New & extended Celery tasks.

This module adds the tasks that back the persistent-retry and
scheduled-upgrade features.  In the full implementation these stubs
would be merged into openwisp_firmware_upgrader/tasks.py and wired up
in apps.py / celery.py.

Every function below contains a detailed docstring **and** pseudocode
comment block so that a mentor can follow the intended implementation
logic without running the code.
"""

import logging
import math
import random

from celery import shared_task
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from openwisp_utils.tasks import OpenwispCeleryTask

from .swapper import load_model

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: exponential back-off with jitter
# ─────────────────────────────────────────────────────────────────────────────

# Configurable via Django settings (full implementation reads from app_settings)
RETRY_BASE_SECONDS = 60  # 1 minute floor
RETRY_MULTIPLIER = 2.0  # double each attempt
RETRY_MAX_SECONDS = 12 * 3600  # hard cap at 12 hours
RETRY_JITTER_FRACTION = 0.25  # ±25 % randomization


def compute_retry_delay(retry_count: int) -> int:
    """
    Return the number of seconds to wait before the next retry attempt.

    Strategy: randomized exponential back-off, capped at RETRY_MAX_SECONDS.

        delay = clamp(base * multiplier^retry_count, base, max)
        delay = delay * uniform(1 - jitter, 1 + jitter)   # add jitter

    Parameters
    ----------
    retry_count : int
        How many times this operation has already been retried.

    Returns
    -------
    int
        Seconds to wait (always between RETRY_BASE_SECONDS and
        RETRY_MAX_SECONDS after jitter is applied).
    """
    # Pseudocode:
    #   raw   = RETRY_BASE_SECONDS * (RETRY_MULTIPLIER ** retry_count)
    #   raw   = min(raw, RETRY_MAX_SECONDS)
    #   jitter_factor = random.uniform(1 - RETRY_JITTER_FRACTION,
    #                                  1 + RETRY_JITTER_FRACTION)
    #   return int(raw * jitter_factor)

    raw = RETRY_BASE_SECONDS * math.pow(RETRY_MULTIPLIER, retry_count)
    raw = min(raw, RETRY_MAX_SECONDS)
    jitter = random.uniform(1 - RETRY_JITTER_FRACTION, 1 + RETRY_JITTER_FRACTION)
    return int(raw * jitter)


# ─────────────────────────────────────────────────────────────────────────────
# Task 1: retry_offline_upgrade
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(base=OpenwispCeleryTask, bind=True)
def retry_offline_upgrade(self, operation_id: str):
    """
    Re-attempt a single UpgradeOperation that was previously paused
    because the device was offline.

    Triggered by
    ~~~~~~~~~~~~
    * The ``handle_device_online`` signal handler when the target device's
      health status changes to "ok" (preferred path via openwisp-monitoring).
    * The ``scan_pending_retries`` periodic task (fallback polling path).

    Full implementation pseudocode
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        operation = UpgradeOperation.objects.get(pk=operation_id)

        IF operation.status != "waiting":
            # Another path already picked this up — skip
            RETURN

        # Acquire a per-device lock to prevent concurrent upgrades
        lock_key = f"firmware-upgrade-device-{operation.device_id}"
        WITH distributed_lock(lock_key, timeout=30):
            operation.refresh_from_db()
            IF operation.status != "waiting":
                RETURN  # lost the race

            # Clear the waiting state, set back to in-progress
            operation.waiting_for_device = False
            operation.status = "in-progress"
            operation.save(update_fields=["waiting_for_device", "status"])

        # Delegate to the existing upgrade_firmware task
        upgrade_firmware.delay(operation_id)

    Parameters
    ----------
    operation_id : str (UUID)
        Primary key of the UpgradeOperation to retry.
    """
    # ── STUB implementation (prototype) ─────────────────────────────────────────
    try:
        operation = load_model("UpgradeOperation").objects.get(pk=operation_id)
    except ObjectDoesNotExist:
        logger.warning(
            "retry_offline_upgrade: UpgradeOperation %s not found", operation_id
        )
        return

    if operation.status != "waiting":
        logger.debug(
            "retry_offline_upgrade: operation %s is no longer waiting (status=%s), skipping",
            operation_id,
            operation.status,
        )
        return

    logger.info(
        "retry_offline_upgrade: retrying operation %s (retry #%d)",
        operation_id,
        operation.retry_count,
    )
    # In the full implementation this would delegate to upgrade_firmware.delay(operation_id)
    # For the prototype we just log the intent.
    operation.log_line(
        f"[PROTOTYPE] Retry #{operation.retry_count} triggered by retry_offline_upgrade task."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: scan_pending_retries
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(base=OpenwispCeleryTask)
def scan_pending_retries():
    """
    Celery Beat periodic task (runs every minute) that scans for
    UpgradeOperations whose ``next_retry_at`` has elapsed and
    dispatches ``retry_offline_upgrade`` for each one.

    This is the *fallback* path for environments where openwisp-monitoring
    is not installed or the health_status_changed signal is not available.

    Celery Beat configuration (full implementation, in apps.py or celery.py)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        CELERY_BEAT_SCHEDULE = {
            "scan-pending-retries": {
                "task": "openwisp_firmware_upgrader.tasks.scan_pending_retries",
                "schedule": crontab(minute="*"),   # every minute
            },
        }

    Full implementation pseudocode
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        now = timezone.now()
        due_operations = UpgradeOperation.objects.filter(
            status="waiting",
            next_retry_at__lte=now,
            batch__persistent=True,
        ).select_related("batch")

        FOR operation IN due_operations:
            # Add randomized delay (1-30 s) to prevent thundering-herd
            jitter_seconds = random.randint(1, 30)
            retry_offline_upgrade.apply_async(
                args=[str(operation.pk)],
                countdown=jitter_seconds,
            )
            logger.info("Scheduled retry for operation %s in %ds", operation.pk, jitter_seconds)
    """
    # ── STUB implementation (prototype) ─────────────────────────────────────────
    now = timezone.now()
    UpgradeOperation = load_model("UpgradeOperation")
    due_qs = UpgradeOperation.objects.filter(
        status="waiting",
        next_retry_at__lte=now,
    )
    count = due_qs.count()
    logger.info("scan_pending_retries: found %d operation(s) due for retry", count)

    for operation in due_qs.iterator():
        jitter = random.randint(1, 30)
        logger.info(
            "scan_pending_retries: scheduling retry for %s in %ds", operation.pk, jitter
        )
        # Full implementation:
        # retry_offline_upgrade.apply_async(args=[str(operation.pk)], countdown=jitter)
        # Prototype: log only
        operation.log_line(
            f"[PROTOTYPE] scan_pending_retries would dispatch retry in {jitter}s."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Task 3: execute_scheduled_batch_upgrades
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(base=OpenwispCeleryTask)
def execute_scheduled_batch_upgrades():
    """
    Celery Beat periodic task (runs every minute) that looks for
    BatchUpgradeOperations whose ``scheduled_at`` has elapsed and
    triggers their execution.

    Design note: We deliberately avoid using Celery ``eta``/``countdown``
    for scheduling far-future tasks because these are unreliable across
    broker restarts.  Instead, we store the target datetime in the DB and
    poll every minute.

    Celery Beat configuration
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        "execute-scheduled-upgrades": {
            "task": "openwisp_firmware_upgrader.tasks.execute_scheduled_batch_upgrades",
            "schedule": crontab(minute="*"),
        }

    Full implementation pseudocode
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        now = timezone.now()
        due_batches = BatchUpgradeOperation.objects.filter(
            status="scheduled",
            scheduled_at__lte=now,
        )

        FOR batch IN due_batches:
            # Re-validate: check firmware still exists, permissions are valid,
            # and there are still devices to upgrade.
            IF NOT batch.has_valid_targets():
                batch.status = "failed"
                batch.save()
                send_notification(
                    batch,
                    message="Scheduled upgrade cancelled: no valid targets at execution time."
                )
                CONTINUE

            # Check for conflicting in-progress or scheduled batches
            # for the same build/organization
            IF batch.has_conflict():
                # Defer by 5 minutes and retry
                batch.scheduled_at = now + timedelta(minutes=5)
                batch.save()
                CONTINUE

            # Transition to in-progress and kick off the batch
            batch.status = "in-progress"
            batch.save()
            send_notification(batch, message="Scheduled upgrade is now starting.")
            batch_upgrade_operation.delay(batch.pk, firmwareless=True)
    """
    # ── STUB implementation (prototype) ─────────────────────────────────────────
    now = timezone.now()
    BatchUpgradeOperation = load_model("BatchUpgradeOperation")
    due_batches = BatchUpgradeOperation.objects.filter(
        status="scheduled",
        scheduled_at__lte=now,
    )
    count = due_batches.count()
    logger.info(
        "execute_scheduled_batch_upgrades: found %d batch(es) ready to execute", count
    )

    for batch in due_batches.iterator():
        logger.info(
            "execute_scheduled_batch_upgrades: [PROTOTYPE] would start batch %s",
            batch.pk,
        )
        # Full implementation: batch_upgrade_operation.delay(batch.pk, firmwareless=True)


# ─────────────────────────────────────────────────────────────────────────────
# Task 4: send_pending_upgrade_reminders
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(base=OpenwispCeleryTask)
def send_pending_upgrade_reminders():
    """
    Celery Beat periodic task that sends admin notifications about devices
    still waiting for a firmware upgrade after an extended period.

    Default frequency: every 2 months (configurable via settings).

    Full implementation pseudocode
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        threshold_days = app_settings.PENDING_UPGRADE_REMINDER_DAYS  # default 60
        cutoff = timezone.now() - timedelta(days=threshold_days)

        # Group waiting operations by batch
        batches_with_pending = (
            BatchUpgradeOperation.objects
            .filter(
                upgradeoperation__status="waiting",
                upgradeoperation__created__lte=cutoff,
                persistent=True,
            )
            .distinct()
        )

        FOR batch IN batches_with_pending:
            pending_count = batch.upgradeoperation_set.filter(status="waiting").count()
            admin_url = build_admin_url(batch)
            generic_notification.send(
                type="firmware_upgrade_pending_reminder",
                actors=batch,
                message=(
                    f"{pending_count} device(s) in batch '{batch}' have been "
                    f"waiting for a firmware upgrade for over {threshold_days} days. "
                    f"Review: {admin_url}"
                ),
                recipients=get_org_admins(batch.build.category.organization),
            )

    Notification type registration (full implementation, in apps.py)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        register_notification_type(
            type_name="firmware_upgrade_pending_reminder",
            verbose_name="Firmware upgrade pending reminder",
            verb="has pending firmware upgrade devices",
        )
    """
    # ── STUB implementation (prototype) ─────────────────────────────────────────
    logger.info(
        "send_pending_upgrade_reminders: [PROTOTYPE] would query batches with "
        "long-pending operations and send generic_notification reminders."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Signal handler: handle_device_online
# ─────────────────────────────────────────────────────────────────────────────


def handle_device_online(sender, instance, **kwargs):
    """
    Signal handler connected to openwisp_monitoring's
    ``health_status_changed`` signal (preferred retry trigger).

    When a device's health status transitions to "ok", this handler
    checks whether the device has any pending UpgradeOperations and,
    if so, dispatches ``retry_offline_upgrade`` for each one.

    Connection (full implementation, in apps.py ready())
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        from openwisp_monitoring.monitoring.signals import health_status_changed

        health_status_changed.connect(
            handle_device_online,
            sender=Device,
            dispatch_uid="firmware_upgrader_device_online",
        )

    Full implementation pseudocode
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ::

        IF instance.monitoring.status != "ok":
            RETURN  # device is not fully healthy yet

        waiting_ops = UpgradeOperation.objects.filter(
            device=instance,
            status="waiting",
            batch__persistent=True,
        )

        FOR operation IN waiting_ops:
            # Use a short random delay to spread load
            jitter = random.randint(5, 60)
            retry_offline_upgrade.apply_async(
                args=[str(operation.pk)],
                countdown=jitter,
            )

    Parameters
    ----------
    sender : type
        The Device model class.
    instance : Device
        The device whose health status changed.
    """
    # ── STUB implementation (prototype) ─────────────────────────────────────────
    new_status = getattr(getattr(instance, "monitoring", None), "status", None)
    if new_status != "ok":
        return

    UpgradeOperation = load_model("UpgradeOperation")
    waiting_ops = UpgradeOperation.objects.filter(
        device=instance,
        status="waiting",
    )
    for op in waiting_ops:
        logger.info(
            "handle_device_online: [PROTOTYPE] device %s came online; "
            "would dispatch retry for operation %s",
            instance.pk,
            op.pk,
        )
