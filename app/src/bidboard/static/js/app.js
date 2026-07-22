/* BidBoard shared UI behaviors: menus, modals, toasts, quit flow. */
"use strict";

/* Robust timestamp parsing. The backend emits two shapes: ISO with a
   +00:00 offset (Python isoformat) and SQLite's "YYYY-MM-DD HH:MM:SS"
   (space-separated UTC, no zone). Normalize both. */
function parseTs(s) {
  if (!s) return null;
  let iso = String(s).replace(" ", "T");
  if (!/[Zz]|[+-]\d\d:?\d\d$/.test(iso)) iso += "Z"; // assume UTC when unzoned
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}
function fmtTs(s, opts) {
  const d = parseTs(s);
  return d
    ? d.toLocaleString([], opts || { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "";
}
window.parseTs = parseTs;
window.fmtTs = fmtTs;

/* ---------- toasts ---------- */
function toast(message, actionHtml) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<span>${message}</span>${actionHtml || ""}`;
  host.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}
window.toast = toast;

/* ---------- overflow menu ---------- */
const menu = document.getElementById("overflow-menu");
if (menu) {
  menu.querySelector(".menu-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    menu.classList.toggle("open");
  });
  document.addEventListener("click", () => menu.classList.remove("open"));
}

/* ---------- modals (generic open/close) ---------- */
function openModal(id) {
  const m = document.getElementById(id);
  if (m) m.classList.add("open");
}
function closeModals() {
  document.querySelectorAll(".modal-backdrop.open").forEach((m) => m.classList.remove("open"));
}
window.openModal = openModal;
window.closeModals = closeModals;

document.addEventListener("click", (e) => {
  if (e.target.matches("[data-close-modal]")) closeModals();
  if (e.target.classList.contains("modal-backdrop")) closeModals();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModals();
});

/* ---------- quit flow ---------- */
const quitBtn = document.getElementById("quit-btn");
if (quitBtn) {
  quitBtn.addEventListener("click", () => openModal("quit-modal"));
}
const quitConfirm = document.getElementById("quit-confirm");
if (quitConfirm) {
  quitConfirm.addEventListener("click", async () => {
    quitConfirm.disabled = true;
    quitConfirm.textContent = "Stopping…";
    try {
      await fetch("/api/quit", { method: "POST" });
    } catch (_) {
      /* server may already be gone - that's fine */
    }
    window.location.href = "/goodbye";
  });
}

/* ---------- help ---------- */
const helpLink = document.getElementById("help-link");
if (helpLink) {
  helpLink.addEventListener("click", (e) => {
    e.preventDefault();
    openModal("help-modal");
  });
}

/* ---------- open spreadsheets folder ---------- */
const openFolderBtn = document.getElementById("open-folder-btn");
if (openFolderBtn) {
  openFolderBtn.addEventListener("click", async () => {
    try {
      const r = await fetch("/api/results/open-folder", { method: "POST" });
      if (!r.ok) throw new Error();
    } catch (_) {
      toast("Couldn't open the folder from here - it's the “Spreadsheets” folder next to RUN.");
    }
  });
}

/* ---------- add-company modal openers ---------- */
document.querySelectorAll("#add-company-btn, [data-open-add]").forEach((btn) => {
  btn.addEventListener("click", () => {
    openModal("add-modal");
    const input = document.getElementById("biz-name");
    if (input) setTimeout(() => input.focus(), 50);
  });
});
