"""
GSoC 2026 Prototype – Admin layer additions.

Patch file containing:
  1. Extended BatchUpgradeConfirmationForm  (persistent checkbox + datetime picker)
  2. Patched BatchUpgradeOperationAdmin     (new list column, cancel scheduled action)
  3. Patched UpgradeOperationAdmin          (retry_count, next_retry_at readonly fields)

In the full implementation these changes would be merged directly into
openwisp_firmware_upgrader/admin.py.  They are kept here for clarity.
"""

import logging
from datetime import timedelta

from django import forms
from django.conf import settings as dj_settings
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from .admin import BatchUpgradeConfirmationForm as _OriginalBatchUpgradeConfirmationForm
from .admin import BatchUpgradeOperationAdmin as _OriginalBatchAdmin
from .admin import UpgradeOperationAdmin as _OriginalUpgradeOperationAdmin

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Extended confirmation form
# ─────────────────────────────────────────────────────────────────────────────


class BatchUpgradeConfirmationForm(_OriginalBatchUpgradeConfirmationForm):
    """
    Extends the stock confirmation form with two new fields:

    persistent
    ----------
    A BooleanField rendered as a checkbox.  Checked by default.
    After the batch is created the widget is replaced with a read-only
    indicator (done in the template) to communicate immutability.

    scheduled_at
    ------------
    An optional DateTimeField rendered as an HTML5 ``datetime-local``
    input.  The label shows the server timezone (e.g. "Schedule for
    (server time: UTC)") so the operator knows the reference frame.
    A small JS snippet (upgrade-selected-confirmation.js) reads the
    browser's ``Intl.DateTimeFormat().resolvedOptions().timeZone``,
    converts the chosen datetime to UTC, and sets a hidden
    ``scheduled_at_utc`` field before form submit.

    Validation rules (mirrors AbstractBatchUpgradeOperation.clean()):
      - If set, must be at least 10 minutes in the future.
      - If set, must not exceed 6 months from now.
    """

    persistent = forms.BooleanField(
        initial=True,
        required=False,
        label=_("Persistent upgrades"),
        help_text=_(
            "Automatically retry devices that are offline at upgrade time "
            "using exponential back-off. "
            "⚠ This setting cannot be changed once the batch is created."
        ),
        widget=forms.CheckboxInput(attrs={"class": "persistent-checkbox"}),
    )

    scheduled_at = forms.DateTimeField(
        required=False,
        label=_("Schedule for"),
        help_text=_(
            "Leave blank to start the upgrade immediately. "
            "Time is interpreted in <strong>your browser's local timezone</strong>; "
            "the hidden field <code>scheduled_at_utc</code> stores it in UTC."
        ),
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "class": "scheduled-at-picker",
                "data-server-tz": "%(tz)s",
            }
        ),
    )

    # Hidden UTC field — populated by JS before submit
    scheduled_at_utc = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_scheduled_at_utc"}),
    )

    class Meta(_OriginalBatchUpgradeConfirmationForm.Meta):
        fields = _OriginalBatchUpgradeConfirmationForm.Meta.fields + (
            "persistent",
            "scheduled_at",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Inject server timezone into the widget's data attributes
        server_tz = getattr(dj_settings, "TIME_ZONE", "UTC")
        self.fields["scheduled_at"].widget.attrs["data-server-tz"] = server_tz
        # Default persistent to True for new forms
        if not self.is_bound:
            self.initial.setdefault("persistent", True)

    def clean_scheduled_at(self):
        """
        Validate the scheduled_at field:
          - Must be at least 10 minutes in the future
          - Must not exceed 6 months out
        Returns the value in the original browser timezone;
        the view layer converts it to UTC via the hidden field.
        """
        value = self.cleaned_data.get("scheduled_at")
        if not value:
            return value
        now = timezone.now()
        # Make naive datetime timezone-aware if needed
        if timezone.is_naive(value):
            value = timezone.make_aware(value)
        if value <= now + timedelta(minutes=10):
            raise forms.ValidationError(
                _("The scheduled time must be at least 10 minutes in the future.")
            )
        if value > now + timedelta(days=180):
            raise forms.ValidationError(
                _("The scheduled time cannot be more than 6 months in the future.")
            )
        return value

    class Media:
        js = list(_OriginalBatchUpgradeConfirmationForm.Media.js) + [
            "firmware-upgrader/js/scheduled-upgrade.js",
        ]
        css = {
            "all": list(_OriginalBatchUpgradeConfirmationForm.Media.css.get("all", []))
            + ["firmware-upgrader/css/scheduled-upgrade.css"]
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Patched BatchUpgradeOperationAdmin
# ─────────────────────────────────────────────────────────────────────────────


class BatchUpgradeOperationAdmin(_OriginalBatchAdmin):
    """
    Extends the stock BatchUpgradeOperationAdmin with:

    List view
    ---------
    • New "persistent" column showing Yes / No.
    • New "scheduled_at" column: shows a 🕐 icon + datetime for scheduled
      batches, or "—" for immediate ones.
    • "Cancel scheduled upgrade" list action (only affects scheduled batches).

    Detail view
    -----------
    • "persistent" field rendered read-only (checkbox-like icon).
    • "scheduled_at" field rendered read-only in the detail form.
    • Status transition log section (scheduled → running → success/failed).
    """

    # ── List display ────────────────────────────────────────────────────────────────────
    list_display = _OriginalBatchAdmin.list_display + [
        "display_persistent",
        "display_scheduled_at",
    ]

    actions = list(getattr(_OriginalBatchAdmin, "actions", [])) + [
        "cancel_scheduled_upgrade",
    ]

    # ── Detail fields ──────────────────────────────────────────────────────────────────
    fields = list(_OriginalBatchAdmin.fields) + [
        "display_persistent_detail",
        "display_scheduled_at_detail",
        "status_transition_log",
    ]
    readonly_fields = list(_OriginalBatchAdmin.readonly_fields) + [
        "display_persistent_detail",
        "display_scheduled_at_detail",
        "status_transition_log",
    ]

    # ── Column: persistent ────────────────────────────────────────────────────────────────

    @admin.display(description=_("Persistent"), ordering="persistent")
    def display_persistent(self, obj):
        if obj.persistent:
            return format_html(
                '<img src="/static/admin/img/icon-yes.svg" alt="Yes"> Yes'
            )
        return format_html('<img src="/static/admin/img/icon-no.svg" alt="No"> No')

    # ── Column: scheduled_at ─────────────────────────────────────────────────────────────

    @admin.display(description=_("Scheduled at"), ordering="scheduled_at")
    def display_scheduled_at(self, obj):
        if not obj.scheduled_at:
            return "–"
        local_dt = timezone.localtime(obj.scheduled_at)
        return format_html(
            '<span title="UTC: {}">🕐 {}</span>',
            obj.scheduled_at.strftime("%Y-%m-%d %H:%M UTC"),
            local_dt.strftime("%Y-%m-%d %H:%M"),
        )

    # ── Detail: persistent ───────────────────────────────────────────────────────────────

    @admin.display(description=_("Persistent upgrades"))
    def display_persistent_detail(self, obj):
        if obj.persistent:
            return format_html(
                '<img src="/static/admin/img/icon-yes.svg" alt="Yes">'
                " <strong>Yes</strong> — offline devices will be retried automatically."
            )
        return format_html(
            '<img src="/static/admin/img/icon-no.svg" alt="No">'
            " No — failed devices will not be retried."
        )

    # ── Detail: scheduled_at ────────────────────────────────────────────────────────────

    @admin.display(description=_("Scheduled at (UTC)"))
    def display_scheduled_at_detail(self, obj):
        if not obj.scheduled_at:
            return _("Immediate (not scheduled)")
        local_dt = timezone.localtime(obj.scheduled_at)
        return format_html(
            "{} <small style='color:#666'>(local) / {} UTC</small>",
            local_dt.strftime("%Y-%m-%d %H:%M"),
            obj.scheduled_at.strftime("%Y-%m-%d %H:%M"),
        )

    # ── Detail: status transition log ───────────────────────────────────────────────

    @admin.display(description=_("Status transition log"))
    def status_transition_log(self, obj):
        """
        Renders a simple timeline of status changes for this batch.

        In the prototype this reads from BatchStatusLog (a lightweight
        model added in the full implementation).  For the demo we render
        a static placeholder that demonstrates the UI intent.
        """
        # Prototype placeholder – real implementation reads BatchStatusLog rows
        transitions = []
        if obj.scheduled_at:
            transitions.append(
                {
                    "status": "scheduled",
                    "label": _("Scheduled"),
                    "ts": obj.created,
                    "icon": "🕐",
                }
            )
        transitions.append(
            {
                "status": "in-progress",
                "label": _("Started / in progress"),
                "ts": obj.modified,
                "icon": "▶",
            }
        )
        if obj.status in ("success", "failed", "cancelled"):
            transitions.append(
                {
                    "status": obj.status,
                    "label": obj.get_status_display(),
                    "ts": obj.modified,
                    "icon": "✓" if obj.status == "success" else "✗",
                }
            )

        rows = []
        for t in transitions:
            local_ts = timezone.localtime(t["ts"]).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(
                format_html(
                    "<li><span class='status-log-icon'>{}</span>"
                    " <strong>{}</strong> — <em>{}</em></li>",
                    t["icon"],
                    t["label"],
                    local_ts,
                )
            )
        return format_html(
            '<ul class="status-transition-log">{}</ul>',
            mark_safe("".join(rows)),
        )

    # ── List action: cancel scheduled upgrade ─────────────────────────────────────────────

    @admin.action(
        description=_("Cancel selected scheduled upgrades"),
        permissions=["change"],
    )
    def cancel_scheduled_upgrade(self, request, queryset):
        scheduled = queryset.filter(status="scheduled")
        count = scheduled.count()
        if not count:
            self.message_user(
                request,
                _("No scheduled upgrades were found in the selection."),
                messages.WARNING,
            )
            return
        scheduled.update(status="cancelled")
        self.message_user(
            request,
            _(f"{count} scheduled upgrade(s) have been cancelled."),
            messages.SUCCESS,
        )

    class Media:
        css = {
            "all": [
                "firmware-upgrader/css/scheduled-upgrade.css",
                "firmware-upgrader/css/status-log.css",
            ]
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Patched UpgradeOperationAdmin
# ─────────────────────────────────────────────────────────────────────────────


class UpgradeOperationAdmin(_OriginalUpgradeOperationAdmin):
    """
    Extends the stock UpgradeOperationAdmin with:

    • retry_count  — read-only integer field.
    • next_retry_at_display — human-friendly "Pending retry at HH:MM" string.
    • waiting_for_device   — shown as a badge in the status column.

    The "waiting" status badge is registered in the template via the
    ``STATUS_BADGE_MAP`` dict injected into template context.
    """

    fields = list(_OriginalUpgradeOperationAdmin.fields) + [
        "retry_count",
        "next_retry_display",
    ]
    readonly_fields = list(_OriginalUpgradeOperationAdmin.readonly_fields) + [
        "retry_count",
        "next_retry_display",
    ]

    @admin.display(description=_("Retry count"))
    def retry_count(self, obj):
        return obj.retry_count

    @admin.display(description=_("Next retry"))
    def next_retry_display(self, obj):
        """
        Shows 'Pending retry at HH:MM (local)' for waiting operations,
        or '–' for operations that are not in a retry state.
        """
        if not getattr(obj, "next_retry_at", None):
            return "–"
        if not obj.waiting_for_device:
            return "–"
        local_dt = timezone.localtime(obj.next_retry_at)
        return format_html(
            '<span class="retry-pending">⏳ Pending retry at {}</span>',
            local_dt.strftime("%H:%M"),
        )

    # ── List display: status badge for 'waiting' ────────────────────────────────────────

    @admin.display(description=_("Status"), ordering="status")
    def display_status_with_badge(self, obj):
        badge_map = {
            "in-progress": ("blue", _("In progress")),
            "waiting": ("orange", _("Waiting for device")),
            "success": ("green", _("Success")),
            "failed": ("red", _("Failed")),
            "cancelled": ("grey", _("Cancelled")),
            "aborted": ("dark-grey", _("Aborted")),
        }
        colour, label = badge_map.get(obj.status, ("grey", obj.get_status_display()))
        return format_html(
            '<span class="status-badge status-badge--{}">{}</span>',
            colour,
            label,
        )

    list_display = [
        col if col != "status" else "display_status_with_badge"
        for col in _OriginalUpgradeOperationAdmin.list_display
    ]
