/* Scan page: trigger a run, then poll for live per-company progress and
   inline flag cards. Polling with an event cursor gives stream-like
   liveness without SSE's connection management. */
"use strict";

const root = document.getElementById("scan-root");
if (root) {
  const runBtn = document.getElementById("run-scan-btn");
  const controls = document.getElementById("scan-controls");
  const overall = document.getElementById("scan-overall");
  const overallBar = document.getElementById("overall-bar");
  const overallText = document.getElementById("overall-text");
  const overallElapsed = document.getElementById("overall-elapsed");
  const headline = document.getElementById("scan-headline");
  const subline = document.getElementById("scan-subline");
  const finished = document.getElementById("scan-finished");
  const finishedHeadline = document.getElementById("finished-headline");
  const finishedSubline = document.getElementById("finished-subline");
  const openWorkbookBtn = document.getElementById("open-workbook-btn");
  const companyRows = document.getElementById("company-rows");
  const flagsBox = document.getElementById("scan-flags");
  const statusText = document.getElementById("status-text");
  const statusPill = document.getElementById("status-pill");

  let cursor = 0;
  let pollTimer = null;
  const rowEls = new Map();
  const seenFlags = new Set();

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  function fmtElapsed(sec) {
    const m = Math.floor(sec / 60), s = sec % 60;
    return m ? `${m}m ${s}s` : `${s}s`;
  }

  function ensureRow(c) {
    let node = rowEls.get(c.company_id);
    if (!node) {
      node = document.createElement("div");
      node.className = "card";
      node.style.padding = "12px 16px";
      companyRows.appendChild(node);
      rowEls.set(c.company_id, node);
    }
    const glyph = c.status === "done"
      ? `<svg class="check-in" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#2F9E44" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>`
      : c.status === "attention"
      ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#B97F10" stroke-width="2.4"><path d="M12 8v5M12 16h.01"/><circle cx="12" cy="12" r="9"/></svg>`
      : c.status === "scanning"
      ? `<span class="spinner"></span>`
      : `<span style="width:16px;height:16px;border-radius:50%;background:var(--surface-2);display:inline-block;"></span>`;
    node.innerHTML = `
      <div class="row between">
        <div class="row" style="gap:12px;flex-wrap:nowrap;">
          ${glyph}
          <div>
            <div style="font-weight:620;">${esc(c.name)}</div>
            <div class="small muted">${esc(c.stage || (c.status === "waiting" ? "Waiting…" : ""))}</div>
          </div>
        </div>
        <div class="small muted">${c.rows ? c.rows + " bids" : ""}${c.flags ? ` · ${c.flags} to review` : ""}</div>
      </div>`;
  }

  function addFlag(ev) {
    const key = `${ev.company_id}:${ev.message}`;
    if (seenFlags.has(key)) return;
    seenFlags.add(key);
    const sev = ev.level === "error" ? "error" : ev.level === "warn" ? "warn" : "info";
    const card = document.createElement("div");
    card.className = `flag-card ${sev}`;
    card.innerHTML = `<div><div class="flag-body">${esc(ev.message)}</div></div>`;
    flagsBox.appendChild(card);
  }

  function render(snap) {
    const run = snap.run;
    if (statusText) {
      if (run.state === "running") {
        statusText.textContent = `Scanning - ${run.done_companies} of ${run.total_companies}`;
        statusPill.classList.remove("idle");
      } else {
        statusText.textContent = "Idle";
        statusPill.classList.add("idle");
      }
    }

    for (const c of run.companies) ensureRow(c);

    if (run.state === "running") {
      controls.hidden = true;
      overall.hidden = false;
      finished.hidden = true;
      const pct = run.total_companies
        ? Math.round((run.done_companies / run.total_companies) * 100) : 5;
      overallBar.style.width = Math.max(pct, 5) + "%";
      overallText.textContent = `Scanning - company ${Math.min(run.done_companies + 1, run.total_companies)} of ${run.total_companies}`;
      overallElapsed.textContent = fmtElapsed(run.elapsed);
      headline.textContent = "Scanning…";
      subline.textContent = "We're reading each company's bid pages one at a time, with a short pause between pages so their websites are treated politely.";
      if (!document.getElementById("cancel-scan-btn")) {
        const btn = document.createElement("button");
        btn.className = "btn btn-ghost";
        btn.id = "cancel-scan-btn";
        btn.textContent = "Cancel run";
        btn.addEventListener("click", async () => {
          if (!confirm("Stop this scan? Nothing is lost - companies already scanned keep their results.")) return;
          await fetch("/api/scans/cancel", { method: "POST" });
        });
        overall.appendChild(btn);
      }
    } else if (run.state === "finished" || run.state === "cancelled") {
      overall.hidden = true;
      controls.hidden = false;
      finished.hidden = false;
      runBtn.disabled = false;
      runBtn.textContent = "Run again";
      const attention = run.companies.filter((c) => c.status === "attention").length;
      finishedHeadline.textContent = run.state === "cancelled" ? "Scan cancelled" : "Done";
      finishedSubline.textContent = run.state === "cancelled"
        ? "Companies already scanned kept their results."
        : `${run.total_rows} bids from ${run.total_companies} compan${run.total_companies === 1 ? "y" : "ies"}` +
          (attention ? ` - ${attention} need${attention === 1 ? "s" : ""} your attention.` : ".");
      if (run.workbook && run.workbook.primary) {
        openWorkbookBtn.hidden = false;
        openWorkbookBtn.onclick = async () => {
          try {
            await fetch("/api/results/open-file", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ path: run.workbook.primary }),
            });
          } catch (_) { toast("Couldn't open the file - check the Spreadsheets folder."); }
        };
      }
      stopPolling();
    }
  }

  async function poll() {
    try {
      const snap = await (await fetch(`/api/scans/current?since=${cursor}`)).json();
      cursor = snap.cursor;
      for (const ev of snap.events) {
        if (ev.level === "warn" || ev.level === "error") addFlag(ev);
      }
      render(snap);
    } catch (_) { /* transient; keep polling */ }
  }

  function startPolling() {
    if (pollTimer) return;
    poll();
    pollTimer = setInterval(poll, 1000);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    runBtn.textContent = "Starting…";
    flagsBox.innerHTML = "";
    seenFlags.clear();
    try {
      const r = await fetch("/api/scans", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (r.status === 409) {
        toast("A scan is already running.");
      }
      startPolling();
    } catch (e) {
      runBtn.disabled = false;
      runBtn.textContent = "Run scan now";
      toast("Couldn't start the scan - try again.");
    }
  });

  // if a scan is already running when the page loads, resume the live view
  poll().then(() => {
    fetch("/api/scans/current?since=0").then((r) => r.json()).then((snap) => {
      if (snap.run.state === "running") { runBtn.disabled = true; startPolling(); }
    });
  });
}
