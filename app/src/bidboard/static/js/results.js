/* Results page: list built workbooks with Open file / Open folder / Rebuild. */
"use strict";

const listEl = document.getElementById("results-list");
const emptyEl = document.getElementById("results-empty");

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function basename(p) {
  return String(p).split(/[\\/]/).pop();
}

async function openFile(path) {
  try {
    const r = await fetch("/api/results/open-file", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error);
  } catch (e) {
    toast(e.message || "Couldn't open the file.");
  }
}

async function openFolder() {
  try { await fetch("/api/results/open-folder", { method: "POST" }); }
  catch (_) { toast("Couldn't open the folder from here."); }
}

async function rebuild(btn) {
  btn.disabled = true;
  btn.textContent = "Rebuilding…";
  try {
    const r = await fetch("/api/results/rebuild", { method: "POST" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error);
    toast("Rebuilt with your current settings.");
    load();
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
    btn.textContent = "Rebuild with current settings";
  }
}

async function load() {
  let builds = [];
  try { builds = await (await fetch("/api/results")).json(); }
  catch (_) { return; }

  if (emptyEl) emptyEl.hidden = builds.length > 0;
  listEl.innerHTML = "";

  builds.forEach((b, idx) => {
    const when = fmtTs(b.created_at);
    const isLatest = idx === 0;
    const card = document.createElement("div");
    card.className = "card";
    const fileRows = b.files.map((f) => {
      const path = typeof f === "string" ? f : f.path;
      const meta = (f && f.scope && f.rows != null)
        ? `<span class="small muted">${esc(f.scope)} · ${f.rows} bids</span>` : "";
      return `
      <div class="row between" style="border:1px solid var(--line);border-radius:8px;padding:8px 12px;">
        <div style="min-width:0;">
          <div class="small" style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(basename(path))}</div>
          ${meta}
        </div>
        <div class="row" style="gap:6px;flex-shrink:0;">
          <button class="btn btn-secondary open-file" data-path="${esc(path)}" style="padding:4px 12px;">Open</button>
          <button class="btn btn-ghost reveal-file" data-path="${esc(path)}" style="padding:4px 10px;" data-tip="Show in folder">Folder</button>
        </div>
      </div>`;
    }).join("");
    card.innerHTML = `
      <div class="row between" style="margin-bottom:var(--sp-3);">
        <div>
          <div class="card-title" style="margin-bottom:0;">${isLatest ? "Latest scan" : "Scan"} - ${esc(when)}</div>
          <div class="card-sub">${b.files.length} file${b.files.length === 1 ? "" : "s"}${b.run_status ? " · " + esc(b.run_status) : ""}</div>
        </div>
        <div class="row">
          <button class="btn btn-secondary" onclick="(${openFolder.toString()})()">Open folder</button>
          ${isLatest ? `<button class="btn btn-primary rebuild-btn">Rebuild with current settings</button>` : ""}
        </div>
      </div>
      <div class="stack" style="--sp-4:6px;">${fileRows}</div>`;
    listEl.appendChild(card);
    card.querySelectorAll(".open-file").forEach((btn) =>
      btn.addEventListener("click", () => openFile(btn.dataset.path)));
    card.querySelectorAll(".reveal-file").forEach((btn) =>
      btn.addEventListener("click", async () => {
        try {
          await fetch("/api/results/reveal-file", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: btn.dataset.path }),
          });
        } catch (_) { openFolder(); }
      }));
    const rb = card.querySelector(".rebuild-btn");
    if (rb) rb.addEventListener("click", () => rebuild(rb));
  });
}

load();
