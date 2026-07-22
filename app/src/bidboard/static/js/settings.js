/* Settings page: load current values, save patches, browser-engine status. */
"use strict";

const form = document.getElementById("settings-form");
if (form) {
  const savedNote = document.getElementById("settings-saved-note");
  const POLITENESS = {
    fast: [1, 3], normal: [2, 5], slow: [4, 8],
  };

  async function load() {
    const s = await (await fetch("/api/settings")).json();
    form.sort_by.value = s.output.sort_by;
    form.group_by.value = s.output.group_by;
    form.charts.value = s.output.charts;
    form.horizon_months.value = String(s.output.horizon_months);
    form.decimals.value = String(s.output.decimals.cash);
    form.schedule_enabled.checked = s.schedule.enabled;
    form.schedule_day.value = s.schedule.day_of_week || "daily";
    form.schedule_hour.value = String(s.schedule.hour ?? 7);
    form.schedule_minute.value = String(s.schedule.minute ?? 30);
    form.respect_robots.checked = s.advanced.respect_robots;
    const min = s.advanced.politeness_min_s;
    form.politeness.value = min <= 1.5 ? "fast" : min >= 4 ? "slow" : "normal";
  }

  async function refreshScheduleHint() {
    const hint = document.getElementById("schedule-next");
    if (!hint) return;
    try {
      const s = await (await fetch("/api/system/schedule-status")).json();
      hint.textContent = s.armed ? `Next automatic scan: ${s.next_run}.` : "";
    } catch (_) { hint.textContent = ""; }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const [pmin, pmax] = POLITENESS[form.politeness.value] || [2, 5];
    const patch = {
      output: {
        sort_by: form.sort_by.value,
        group_by: form.group_by.value,
        charts: form.charts.value,
        horizon_months: parseInt(form.horizon_months.value, 10),
        decimals: {
          cash: parseInt(form.decimals.value, 10),
          basis: parseInt(form.decimals.value, 10),
          futures: parseInt(form.decimals.value, 10),
        },
      },
      schedule: {
        enabled: form.schedule_enabled.checked,
        day_of_week: form.schedule_day.value,
        hour: parseInt(form.schedule_hour.value, 10),
        minute: parseInt(form.schedule_minute.value, 10),
      },
      advanced: {
        respect_robots: form.respect_robots.checked,
        politeness_min_s: pmin,
        politeness_max_s: pmax,
      },
    };
    const btn = document.getElementById("save-settings-btn");
    btn.disabled = true;
    try {
      const r = await fetch("/api/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Couldn't save.");
      savedNote.textContent = "Saved.";
      setTimeout(() => (savedNote.textContent = ""), 2500);
      refreshScheduleHint();
    } catch (err) {
      savedNote.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  load().then(refreshScheduleHint);
}
