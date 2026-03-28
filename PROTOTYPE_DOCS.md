# GSoC 2026 Prototype Documentation

**Project:** Persistent & Scheduled Firmware Upgrades for OpenWISP Firmware Upgrader
**Applicant:** Aditya Shandilya
**Branch:** `gsoc-2026-prototype`
**Issues:** [#379](https://github.com/openwisp/openwisp-firmware-upgrader/issues/379) · [#380](https://github.com/openwisp/openwisp-firmware-upgrader/issues/380)

---

## What this prototype covers (built)

| Area                                                               | Files                                                                           |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| DB migration (fields + status extensions)                          | `openwisp_firmware_upgrader/migrations/0018_persistent_scheduled_upgrades.py`   |
| Model mixins (field definitions, validation, helpers)              | `openwisp_firmware_upgrader/base/models_gsoc_patch.py`                          |
| Admin form + admin classes (list columns, confirmation form)       | `openwisp_firmware_upgrader/admin_gsoc_patch.py`                                |
| Confirmation page template (persistent checkbox + datetime picker) | `openwisp_firmware_upgrader/templates/admin/upgrade_selected_confirmation.html` |
| JavaScript (live validation, browser→UTC conversion)               | `openwisp_firmware_upgrader/static/firmware-upgrader/js/scheduled-upgrade.js`   |
| CSS (status badges, retry indicator, timeline log)                 | `openwisp_firmware_upgrader/static/firmware-upgrader/css/scheduled-upgrade.css` |
| Stub Celery tasks (with full pseudocode)                           | `openwisp_firmware_upgrader/tasks_gsoc_patch.py`                                |

---

## What this prototype does NOT build (documented below)

The sections below describe every piece of backend logic that would be
implemented in the real GSoC project but is outside the UI-demo scope.
Each section contains **pseudocode** that is close enough to real Django/Celery
code to be unambiguously implementable.

---

## Part 1 – Persistent Mass Upgrades (#379)

### 1.1 Retry logic inside `UpgradeOperation.upgrade()`

**Where:** `openwisp_firmware_upgrader/base/models.py` — inside
`AbstractUpgradeOperation.upgrade()`.

When the device cannot be reached (`NoWorkingDeviceConnectionError`) and the
parent batch has `persistent=True`, instead of immediately marking the
operation as failed, we schedule a retry.

```python
# Pseudocode – replaces the existing failure branch for NoWorkingDeviceConnectionError

def upgrade(self, recoverable=True):
    # ... existing code to get conn ...
    try:
        conn = DeviceConnection.get_working_connection(self.device)
    except NoWorkingDeviceConnectionError:
        # ── NEW: persistent retry path ───────────────────────────────────
        if self._should_retry_persistently():
            delay = compute_retry_delay(self.retry_count)  # from tasks_gsoc_patch.py
            self.schedule_retry(
                batch_persistent=self.batch.persistent,
                delay_seconds=delay,
            )
            logger.info(
                "Device %s offline; scheduling retry #%d in %ds",
                self.device_id, self.retry_count, delay,
            )
            return
        # ── Existing failure path (non-persistent) ────────────────────────
        self.status = "failed"
        self.log_line("No working device connection; not persistent.")
        self.save()
        return

def _should_retry_persistently(self):
    """True when the parent batch is persistent and not cancelled."""
    return (
        self.batch is not None
        and self.batch.persistent
        and self.batch.status not in ("cancelled", "failed")
    )
```

### 1.2 `health_status_changed` signal connection

**Where:** `openwisp_firmware_upgrader/apps.py` — inside `FirmwareUpgraderConfig.ready()`.

```python
# Pseudocode

def ready(self):
    super().ready()
    # ... existing signal connections ...

    # ── NEW: connect to openwisp-monitoring health_status_changed ─────────
    try:
        from openwisp_monitoring.monitoring.signals import health_status_changed
        from .tasks_gsoc_patch import handle_device_online
        Device = swapper.load_model("config", "Device")
        health_status_changed.connect(
            handle_device_online,
            sender=Device,
            dispatch_uid="firmware_upgrader_device_online",
        )
    except ImportError:
        # openwisp-monitoring not installed; fallback to periodic polling
        logger.info(
            "openwisp-monitoring not available; "
            "falling back to scan_pending_retries periodic task."
        )
```

### 1.3 Celery Beat periodic tasks registration

**Where:** `openwisp_firmware_upgrader/apps.py` or project `celery.py`.

```python
# Pseudocode – add to CELERY_BEAT_SCHEDULE

CELERY_BEAT_SCHEDULE.update({
    # Fallback retry scanner (runs every minute)
    "firmware-upgrader-scan-pending-retries": {
        "task": "openwisp_firmware_upgrader.tasks.scan_pending_retries",
        "schedule": crontab(minute="*"),
    },
    # Scheduled batch executor (runs every minute)
    "firmware-upgrader-execute-scheduled": {
        "task": "openwisp_firmware_upgrader.tasks.execute_scheduled_batch_upgrades",
        "schedule": crontab(minute="*"),
    },
    # Reminder notifications (every 2 months)
    "firmware-upgrader-pending-reminders": {
        "task": "openwisp_firmware_upgrader.tasks.send_pending_upgrade_reminders",
        "schedule": crontab(minute=0, hour=9, day_of_month="1"),   # monthly on day 1
        # In full impl: make frequency configurable via app_settings
    },
})
```

### 1.4 Concurrency guard: one upgrade per device at a time

Already present in `AbstractUpgradeOperation.upgrade()` via the
`qs.exists()` check. The full implementation adds a distributed Redis lock
to handle the gap between signal receipt and DB update:

```python
# Pseudocode — inside handle_device_online signal handler

from django_redis import get_redis_connection

def handle_device_online(sender, instance, **kwargs):
    if instance.monitoring.status != "ok":
        return

    redis = get_redis_connection("default")
    lock_key = f"fw-upgrader-device-online-{instance.pk}"

    # Atomic lock: prevents duplicate retry dispatches from concurrent signals
    acquired = redis.set(lock_key, "1", ex=30, nx=True)
    if not acquired:
        logger.debug("Device %s: online handler already running, skipping", instance.pk)
        return

    try:
        waiting_ops = UpgradeOperation.objects.filter(
            device=instance, status="waiting", batch__persistent=True
        )
        for op in waiting_ops:
            jitter = random.randint(5, 60)
            retry_offline_upgrade.apply_async(args=[str(op.pk)], countdown=jitter)
    finally:
        redis.delete(lock_key)
```

### 1.5 `generic_notification` for admin reminders

```python
# Pseudocode — inside send_pending_upgrade_reminders task

from openwisp_notifications.tasks import send_notification

def send_pending_upgrade_reminders():
    threshold = timedelta(days=app_settings.PENDING_UPGRADE_REMINDER_DAYS)
    cutoff    = timezone.now() - threshold

    batches = (
        BatchUpgradeOperation.objects
        .filter(
            upgradeoperation__status="waiting",
            upgradeoperation__modified__lte=cutoff,
            persistent=True,
        )
        .distinct()
    )

    for batch in batches:
        pending_count = batch.upgradeoperation_set.filter(status="waiting").count()
        org_admins = (
            User.objects
            .filter(
                openwisp_users_organization__organization=batch.build.category.organization,
                openwisp_users_organization__is_admin=True,
            )
        )
        for admin_user in org_admins:
            send_notification.delay(
                type="fw_pending_reminder",
                recipient_ids=[admin_user.pk],
                actor_object_id=str(batch.pk),
                actor_content_type_id=ContentType.objects.get_for_model(batch).pk,
                description=(
                    f"{pending_count} device(s) in '{batch}' have been "
                    f"waiting for a firmware upgrade for over "
                    f"{app_settings.PENDING_UPGRADE_REMINDER_DAYS} days."
                ),
            )
```

---

## Part 2 – Scheduled Mass Upgrades (#380)

### 2.1 View layer: handling the `scheduled_at_utc` hidden field

**Where:** `openwisp_firmware_upgrader/admin.py` — inside `BuildAdmin.upgrade_selected()`.

The existing view handles `upgrade_all` / `upgrade_related` POST flags.
The full implementation extends this to also parse the hidden UTC field:

```python
# Pseudocode — inside upgrade_selected() action view

def upgrade_selected(self, request, queryset):
    # ... existing boilerplate ...

    if upgrade_all or upgrade_related:
        scheduled_at_utc_raw = request.POST.get("scheduled_at_utc", "")
        scheduled_at = None

        if scheduled_at_utc_raw:
            try:
                # Parse ISO-8601 UTC string sent by the JS snippet
                scheduled_at = datetime.fromisoformat(
                    scheduled_at_utc_raw.replace("Z", "+00:00")
                )
                if timezone.is_naive(scheduled_at):
                    scheduled_at = timezone.make_aware(scheduled_at, timezone.utc)
            except ValueError:
                self.message_user(request, "Invalid scheduled_at value.", messages.ERROR)
                return

        # Conflict prevention: block if there's already a pending/in-progress
        # batch for the same build
        conflicting = BatchUpgradeOperation.objects.filter(
            build=build,
            status__in=["scheduled", "idle", "in-progress"],
        ).exists()
        if conflicting:
            self.message_user(
                request,
                "A pending or scheduled upgrade already exists for this build.",
                messages.ERROR,
            )
            return

        batch = BatchUpgradeOperation(
            build=build,
            persistent=form.cleaned_data.get("persistent", True),
            scheduled_at=scheduled_at,
            status="scheduled" if scheduled_at else "idle",
            upgrade_options=form.cleaned_data.get("upgrade_options", {}),
            group=form.cleaned_data.get("group"),
            location=form.cleaned_data.get("location"),
        )
        batch.full_clean()
        batch.save()

        if not scheduled_at:
            # Immediate execution: delegate to existing task
            transaction.on_commit(
                partial(batch_upgrade_operation.delay, batch.pk, firmwareless=upgrade_all)
            )
        # If scheduled, the execute_scheduled_batch_upgrades periodic task will handle it.

        return redirect(batch_change_url)
```

### 2.2 Runtime validation at scheduled execution time

**Where:** `openwisp_firmware_upgrader/tasks.py` — inside
`execute_scheduled_batchUpgrades`.

```python
# Pseudocode — inside the execute_scheduled_batch_upgrades task

def has_valid_targets(batch):
    """
    Re-evaluates whether there are still devices to upgrade.
    Called at actual execution time (not at scheduling time).
    """
    dry_run = BatchUpgradeOperation.dry_run(
        build=batch.build,
        group=batch.group,
        location=batch.location,
    )
    firmwares_exist  = dry_run["device_firmwares"].exists()
    devices_exist    = dry_run["devices"].exists()
    firmware_images_exist = batch.build.firmwareimage_set.exists()

    return firmware_images_exist and (firmwares_exist or devices_exist)

def execute_scheduled_batch_upgrades():
    now = timezone.now()
    for batch in BatchUpgradeOperation.objects.filter(
        status="scheduled", scheduled_at__lte=now
    ):
        if not has_valid_targets(batch):
            batch.status = "failed"
            batch.save()
            # Notify org admins
            send_notification(batch, "Scheduled upgrade cancelled: no valid targets.")
            continue

        batch.status = "in-progress"
        batch.save()
        send_notification(batch, "Scheduled upgrade is now starting.")
        batch_upgrade_operation.apply_async(args=[batch.pk, True])
```

### 2.3 REST API extensions

**Where:** `openwisp_firmware_upgrader/api/serializers.py` and
`openwisp_firmware_upgrader/api/views.py`.

```python
# Pseudocode — BatchUpgradeOperationSerializer

class BatchUpgradeOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BatchUpgradeOperation
        fields = [
            # ... existing fields ...
            "persistent",       # read-only after creation
            "scheduled_at",     # nullable datetime (UTC)
            "status",           # now includes "scheduled"
        ]
        read_only_fields = ["persistent"]   # immutable after creation


# Pseudocode — UpgradeOperationSerializer (additions)

class UpgradeOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = UpgradeOperation
        fields = [
            # ... existing fields ...
            "retry_count",
            "next_retry_at",
            "waiting_for_device",
        ]
        read_only_fields = ["retry_count", "next_retry_at", "waiting_for_device"]


# Pseudocode — cancel_scheduled_upgrade API endpoint

class CancelScheduledBatchUpgradeView(APIView):
    permission_classes = [IsAuthenticated, DjangoModelPermissions]

    def post(self, request, pk):
        batch = get_object_or_404(BatchUpgradeOperation, pk=pk, status="scheduled")
        self.check_object_permissions(request, batch)
        batch.status = "cancelled"
        batch.save()
        return Response({"detail": "Scheduled upgrade cancelled."}, status=200)
```

### 2.4 `BatchStatusLog` model (status transition log)

```python
# Pseudocode — new model to power the admin status timeline

class BatchStatusLog(TimeStampedEditableModel):
    """
    Records every status transition for a BatchUpgradeOperation.
    Powers the read-only "Status transition log" shown in the detail view.
    """
    batch = models.ForeignKey(
        "BatchUpgradeOperation",
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    from_status = models.CharField(max_length=12, blank=True)
    to_status   = models.CharField(max_length=12)
    timestamp   = models.DateTimeField(default=timezone.now)
    note        = models.TextField(blank=True)

    class Meta:
        ordering = ["timestamp"]


# Hook into BatchUpgradeOperation.save():

def save(self, *args, **kwargs):
    is_status_change = (
        self.pk and
        self.__class__.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        != self.status
    )
    super().save(*args, **kwargs)
    if is_status_change:
        BatchStatusLog.objects.create(
            batch=self,
            from_status=original_status,  # captured before save
            to_status=self.status,
        )
```

---

## Part 3 – Testing strategy (not implemented in prototype)

### 3.1 Unit tests

```python
# Pseudocode — test_persistent_retry in openwisp_firmware_upgrader/tests/test_models.py

class TestPersistentRetry(TestCase):

    def test_offline_device_schedules_retry(self):
        """
        When upgrade() encounters a NoWorkingDeviceConnectionError and the
        batch is persistent, the operation should transition to 'waiting'
        and next_retry_at should be set.
        """
        batch = create_batch(persistent=True)
        operation = create_operation(batch
```
