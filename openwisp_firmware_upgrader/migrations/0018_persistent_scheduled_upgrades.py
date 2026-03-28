"""
Migration 0018: Add persistent & scheduled upgrade fields.

Adds to BatchUpgradeOperation:
  - persistent (bool, default True, immutable after creation)
  - scheduled_at (datetime, nullable — None means "immediate")

Adds to UpgradeOperation:
  - retry_count (int, default 0)
  - next_retry_at (datetime, nullable)
  - waiting_for_device (bool, default False)
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("firmware_upgrader", "0017_alter_batchupgradeoperation_status"),
    ]

    operations = [
        # ── BatchUpgradeOperation ─────────────────────────────────────────────────────
        migrations.AddField(
            model_name="batchupgradeoperation",
            name="persistent",
            field=models.BooleanField(
                default=True,
                verbose_name="persistent",
                help_text=(
                    "When enabled, devices that are offline at upgrade time will "
                    "be retried automatically using exponential back-off until the "
                    "admin cancels the batch or every device succeeds. "
                    "This flag is immutable once the batch has been created."
                ),
            ),
        ),
        migrations.AddField(
            model_name="batchupgradeoperation",
            name="scheduled_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                default=None,
                verbose_name="scheduled at",
                help_text=(
                    "UTC datetime at which this batch should start executing. "
                    "Leave blank for immediate execution."
                ),
            ),
        ),
        # Extend the STATUS_CHOICES to include 'scheduled'
        migrations.AlterField(
            model_name="batchupgradeoperation",
            name="status",
            field=models.CharField(
                max_length=12,
                choices=[
                    ("idle", "idle"),
                    ("scheduled", "scheduled"),
                    ("in-progress", "in progress"),
                    ("success", "completed successfully"),
                    ("failed", "completed with some failures"),
                    ("cancelled", "completed with some cancellations"),
                ],
                default="idle",
            ),
        ),
        # ── UpgradeOperation ──────────────────────────────────────────────────────
        migrations.AddField(
            model_name="upgradeoperation",
            name="retry_count",
            field=models.PositiveIntegerField(
                default=0,
                verbose_name="retry count",
                help_text="Number of times this operation has been retried because the device was offline.",
            ),
        ),
        migrations.AddField(
            model_name="upgradeoperation",
            name="next_retry_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                default=None,
                verbose_name="next retry at",
                help_text="Scheduled datetime for the next retry attempt (UTC).",
            ),
        ),
        migrations.AddField(
            model_name="upgradeoperation",
            name="waiting_for_device",
            field=models.BooleanField(
                default=False,
                verbose_name="waiting for device",
                help_text=(
                    "True when this operation is paused waiting for the device "
                    "to come back online before the next retry attempt."
                ),
            ),
        ),
        # Extend STATUS_CHOICES for UpgradeOperation to include 'waiting'
        migrations.AlterField(
            model_name="upgradeoperation",
            name="status",
            field=models.CharField(
                max_length=12,
                choices=[
                    ("in-progress", "in progress"),
                    ("waiting", "waiting for device"),
                    ("success", "success"),
                    ("failed", "failed"),
                    ("cancelled", "cancelled"),
                    ("aborted", "aborted"),
                ],
                default="in-progress",
            ),
        ),
    ]
