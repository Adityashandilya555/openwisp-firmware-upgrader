/**
 * scheduled-upgrade.js
 * GSoC 2026 Prototype — Scheduled & Persistent Upgrades UI helpers.
 *
 * Responsibilities
 * ────────────────
 * 1. Display the server timezone next to the datetime picker label.
 * 2. Provide live inline validation for the scheduled_at field:
 *      - Past datetime            → error
 *      - Less than 10 min ahead  → error
 *      - More than 6 months out  → error
 *      - Otherwise               → success hint
 * 3. Convert the browser-local datetime-local value to UTC ISO-8601
 *    before the form submits (stored in the hidden `scheduled_at_utc` field).
 * 4. Grey-out the persistent checkbox after the form has been submitted
 *    (to visually communicate immutability in the confirmation page).
 *
 * This file is included by BatchUpgradeConfirmationForm.Media.
 */
(function ($) {
  "use strict";

  // ── Constants ──────────────────────────────────────────────────────────────
  var MIN_DELAY_MS = 10 * 60 * 1000; // 10 minutes
  var MAX_HORIZON_MS = 6 * 30 * 24 * 3600 * 1000; // ~6 months

  // ── Selectors ─────────────────────────────────────────────────────────────
  var FORM_SEL = "#mass-upgrade-form";
  var PICKER_SEL = ".scheduled-at-picker";
  var UTC_HIDDEN_SEL = "#id_scheduled_at_utc";
  var VALIDATION_MSG_SEL = ".scheduled-validation-msg";
  var PERSISTENT_CB_SEL = ".persistent-checkbox";
  var IMMUTABLE_NOTICE_SEL = ".persistent-immutable-notice";
  var SERVER_TZ_SEL = "[data-server-tz]";

  // ── Helpers ───────────────────────────────────────────────────────────────

  /**
   * Return the browser's IANA timezone string (e.g. "Europe/Rome").
   * Falls back to offset string like "UTC+5:30" if Intl is unavailable.
   */
  function getBrowserTimezone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (e) {
      var offset = -new Date().getTimezoneOffset();
      var sign = offset >= 0 ? "+" : "-";
      var abs = Math.abs(offset);
      return "UTC" + sign + Math.floor(abs / 60) + ":" + ("0" + (abs % 60)).slice(-2);
    }
  }

  /**
   * Validate a datetime-local value string against the business rules.
   * Returns { valid: bool, message: string }
   */
  function validateScheduledAt(rawValue) {
    if (!rawValue) {
      return { valid: true, message: "" };
    }

    var selectedMs = new Date(rawValue).getTime();
    var nowMs = Date.now();

    if (isNaN(selectedMs)) {
      return { valid: false, message: gettext("Invalid date/time value.") };
    }

    if (selectedMs <= nowMs) {
      return {
        valid: false,
        message: gettext("The scheduled time must be in the future."),
      };
    }

    if (selectedMs - nowMs < MIN_DELAY_MS) {
      return {
        valid: false,
        message: gettext("The scheduled time must be at least 10 minutes from now."),
      };
    }

    if (selectedMs - nowMs > MAX_HORIZON_MS) {
      return {
        valid: false,
        message: gettext(
          "The scheduled time cannot be more than 6 months in the future.",
        ),
      };
    }

    // Format the UTC equivalent for the success hint
    var utcStr = new Date(rawValue).toUTCString();
    return {
      valid: true,
      message: gettext("Valid. Will execute at: ") + utcStr + " (UTC)",
    };
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  $(document).ready(function () {
    var $form = $(FORM_SEL);
    var $picker = $(PICKER_SEL);
    var $utcHidden = $(UTC_HIDDEN_SEL);
    var $validationMsg = $(VALIDATION_MSG_SEL);
    var $persistentCb = $(PERSISTENT_CB_SEL);
    var $immutableNotice = $(IMMUTABLE_NOTICE_SEL);

    if (!$form.length) return;

    // ── 1. Annotate the picker with the browser timezone hint ─────────────
    var browserTz = getBrowserTimezone();
    $picker
      .closest(".form-row")
      .find("label")
      .append(
        $('<span class="browser-tz-hint">').text(
          " (" + gettext("your timezone") + ": " + browserTz + ")",
        ),
      );

    // ── 2. Live validation ────────────────────────────────────────────────
    $picker.on("change input", function () {
      var result = validateScheduledAt($(this).val());
      $validationMsg
        .text(result.message)
        .removeClass("success error")
        .addClass(
          result.valid && result.message ? "success" : result.message ? "error" : "",
        );
    });

    // ── 3. UTC conversion on submit ───────────────────────────────────────
    $form.on("submit", function (e) {
      var rawValue = $picker.val();
      if (rawValue) {
        var result = validateScheduledAt(rawValue);
        if (!result.valid) {
          e.preventDefault();
          $picker.focus();
          return;
        }
        // Convert to UTC ISO string
        $utcHidden.val(new Date(rawValue).toISOString());
      }
    });

    // ── 4. Persistent checkbox: grey-out on page load if batch exists ─────
    // In the prototype, we detect a pre-existing batch by checking if the
    // form has a data-batch-pk attribute (set by the template when editing).
    var batchPk = $form.data("batch-pk");
    if (batchPk) {
      $persistentCb
        .prop("disabled", true)
        .attr(
          "title",
          gettext("This setting cannot be changed after the batch has been created."),
        );
      $immutableNotice.show();
    }
  });
})(django.jQuery);
