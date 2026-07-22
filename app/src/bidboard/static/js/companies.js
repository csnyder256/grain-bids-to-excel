/* Companies page: card grid, add-company flow, filters drawer,
   locations (re-point, discover, remove). */
"use strict";

const grid = document.getElementById("company-grid");
const emptyState = document.getElementById("companies-empty");
const countEl = document.getElementById("company-count");

/* ---------- helpers ---------- */
async function api(path, options) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function initials(name) {
  return name.split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase();
}

function hue(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

/* ---------- card grid ---------- */
async function refresh() {
  let companies = [];
  try {
    companies = await api("/api/companies");
  } catch (e) {
    toast("Couldn't load your companies - try refreshing the page.");
    return;
  }
  grid.innerHTML = "";
  if (emptyState) emptyState.hidden = companies.length > 0;
  countEl.textContent = companies.length
    ? `${companies.length} compan${companies.length === 1 ? "y" : "ies"} being watched`
    : "";

  for (const c of companies) grid.appendChild(card(c));
}

function card(c) {
  const locCount = c.sites.filter((s) => s.enabled).length;
  const scanned = c.last_scan_at
    ? `last read ${fmtTs(c.last_scan_at)}`
    : "not scanned yet";
  const node = el(`
    <div class="card" data-id="${c.id}">
      <div class="row between" style="align-items:flex-start;">
        <div class="row" style="gap: var(--sp-3); flex-wrap: nowrap;">
          <div style="width:42px;height:42px;border-radius:10px;display:grid;place-items:center;
                      font-weight:700;color:#fff;background:hsl(${hue(c.name)} 32% 42%);flex-shrink:0;">
            ${esc(initials(c.name))}
          </div>
          <div>
            <div class="card-title" style="margin-bottom:0;">${esc(c.name)}</div>
            <div class="card-sub">${locCount} location${locCount === 1 ? "" : "s"} · ${scanned}</div>
          </div>
        </div>
        ${c.open_flags ? `<span class="badge badge-aging"><span class="dot"></span>${c.open_flags} to review</span>` : ""}
      </div>
      <div class="row" style="margin-top: var(--sp-4);">
        <button class="btn btn-secondary btn-filters">What are we looking for?</button>
        <button class="btn btn-ghost btn-remove" data-tip="Stop watching this company">Remove…</button>
      </div>
    </div>`);
  node.querySelector(".btn-filters").addEventListener("click", () => openFilters(c));
  node.querySelector(".btn-remove").addEventListener("click", () => removeCompany(c));
  return node;
}

async function removeCompany(c) {
  if (!confirm(`Remove ${c.name}? Their past scan history stays in your Results, but they won't be scanned anymore.`)) return;
  try {
    await api(`/api/companies/${c.id}`, { method: "DELETE" });
    toast(`${esc(c.name)} removed.`);
    refresh();
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- add-company flow ---------- */
const searchBtn = document.getElementById("add-search-btn");
const nameInput = document.getElementById("biz-name");
const resultsBox = document.getElementById("add-results");

function skeleton(lines) {
  return `<div class="stack" style="margin-top:var(--sp-3);">${
    Array.from({ length: lines }, () =>
      `<div style="height:52px;border-radius:8px;background:linear-gradient(90deg,var(--surface-2) 25%,#EDE8DE 40%,var(--surface-2) 55%);background-size:200% 100%;animation:sheen 1.2s linear infinite;"></div>`
    ).join("")}</div>`;
}

async function runSearch() {
  const name = nameInput.value.trim();
  if (!name) { nameInput.focus(); return; }
  searchBtn.disabled = true;
  resultsBox.innerHTML = `<p class="small muted" style="margin-top:var(--sp-3);">Searching for “${esc(name)}” and checking for cash bid pages…</p>` + skeleton(3);
  try {
    const data = await api("/api/companies/search-business", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    if (data.mode === "url") {
      renderProbeConfirm(name, data.probe);
    } else {
      renderCandidates(name, data.candidates, data.flags);
    }
  } catch (e) {
    resultsBox.innerHTML = `<div class="flag-card error"><div><div class="flag-title">The search didn't go through</div>
      <div class="flag-body">${esc(e.message)} You can also paste the company's website address instead.</div></div></div>`;
  } finally {
    searchBtn.disabled = false;
  }
}

function renderCandidates(name, candidates, flags) {
  resultsBox.innerHTML = "";
  if (flags && flags.length) {
    for (const f of flags) {
      resultsBox.appendChild(el(`<div class="flag-card ${f.severity === "error" ? "error" : "warn"}">
        <div><div class="flag-title">${esc(f.message)}</div>
        <div class="flag-body">${esc(f.suggestion)}</div></div></div>`));
    }
  }
  if (!candidates.length) {
    if (!flags.length) {
      resultsBox.appendChild(el(`<p class="small muted">Nothing matched. Try the full company name, or paste their website address.</p>`));
    }
    return;
  }
  resultsBox.appendChild(el(`<p class="small muted" style="margin:var(--sp-3) 0 var(--sp-2);">Is one of these your company? Click it to continue.</p>`));
  for (const c of candidates) {
    const evidence = (c.evidence || []).join(" · ");
    const node = el(`
      <button class="card" style="width:100%;text-align:left;cursor:pointer;margin-bottom:var(--sp-2);border-color:var(--line);">
        <div class="card-title" style="margin-bottom:2px;">${esc(c.title || c.domain)}</div>
        <div class="card-sub">${esc(c.domain)}${evidence ? " - " + esc(evidence) : ""}</div>
      </button>`);
    node.addEventListener("click", () => confirmCandidate(name, c));
    resultsBox.appendChild(node);
  }
}

async function confirmCandidate(name, candidate) {
  resultsBox.innerHTML = `<p class="small muted" style="margin-top:var(--sp-3);">Taking a closer look at ${esc(candidate.domain)}…</p>` + skeleton(2);
  const probeTarget = candidate.suggested_bid_page || candidate.url;
  let probe;
  try {
    probe = await api("/api/companies/probe-url", {
      method: "POST",
      body: JSON.stringify({ url: probeTarget }),
    });
  } catch (e) {
    probe = { ok: false, summary: e.message, bid_links: [] };
  }
  renderProbeConfirm(name, probe, candidate);
}

function renderProbeConfirm(name, probe, candidate) {
  const pages = [];
  if (probe.ok && probe.looks_like_bid_page) pages.push(probe.url);
  for (const l of probe.bid_links || []) {
    if (l.score >= 5 && !pages.includes(l.url)) pages.push(l.url);
  }
  const displayName = candidate && candidate.title && !/^https?:/.test(name) ? name : name.replace(/^https?:\/\/(www\.)?/, "").split("/")[0];

  resultsBox.innerHTML = "";
  resultsBox.appendChild(el(`
    <div class="card" style="border-color:#CFE3CA;background:#FBFDF9;">
      <div class="card-title">${esc(probe.summary || "Here's what we found.")}</div>
      ${pages.length
        ? `<div class="card-sub" style="margin-bottom:var(--sp-3);">We'll watch ${pages.length} page${pages.length === 1 ? "" : "s"} for this company. You can add or remove pages any time.</div>`
        : `<div class="card-sub" style="margin-bottom:var(--sp-3);">No bid pages spotted yet - add the company now and point us at the right page afterwards.</div>`}
      <div class="small muted" style="max-height:130px;overflow:auto;margin-bottom:var(--sp-3);">
        ${pages.map((p) => `<div>• ${esc(p)}</div>`).join("")}
      </div>
      <div class="field">
        <label for="confirm-name">Company name (how it appears in your spreadsheets)</label>
        <input class="input" id="confirm-name" value="${esc(displayName)}">
      </div>
      <div class="row" style="justify-content:flex-end;">
        <button class="btn btn-secondary" id="back-to-search">Back</button>
        <button class="btn btn-primary" id="confirm-add">Add company</button>
      </div>
    </div>`));

  document.getElementById("back-to-search").addEventListener("click", runSearch);
  document.getElementById("confirm-add").addEventListener("click", async () => {
    const finalName = document.getElementById("confirm-name").value.trim() || displayName;
    const btn = document.getElementById("confirm-add");
    btn.disabled = true;
    btn.textContent = "Adding…";
    try {
      await api("/api/companies", {
        method: "POST",
        body: JSON.stringify({
          name: finalName,
          homepage_url: candidate ? `https://${candidate.domain}` : probe.url,
          sites: pages.map((p) => ({ url: p })),
        }),
      });
      closeModals();
      resultsBox.innerHTML = "";
      nameInput.value = "";
      toast(`${esc(finalName)} added. Next: run a scan.`, `<a href="/scan">Run a scan</a>`);
      refresh();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Add company";
      toast(e.message);
    }
  });
}

if (searchBtn) {
  searchBtn.addEventListener("click", runSearch);
  nameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); runSearch(); }
  });
}

/* ---------- filters drawer ---------- */
async function openFilters(company) {
  openModal("filters-modal");
  const body = document.getElementById("filters-body");
  body.innerHTML = skeleton(3);
  let data;
  try {
    data = await api(`/api/companies/${company.id}/filters`);
  } catch (e) {
    body.innerHTML = `<p class="muted">${esc(e.message)}</p>`;
    return;
  }
  const chosen = new Set((data.override.commodities || []));
  const options = data.options.commodities;

  body.innerHTML = "";
  body.appendChild(el(`
    <div>
      <p class="muted small">These choices apply to <strong>${esc(company.name)}</strong> only, and they're remembered between sessions.</p>
      <div class="field">
        <label>Commodities to include</label>
        ${options.length
          ? `<div id="commodity-checks">${options.map((o) => `
              <label class="check"><input type="checkbox" value="${esc(o)}" ${chosen.has(o) || !chosen.size ? "checked" : ""}> ${esc(o)}</label>`).join("")}</div>
             <div class="hint">Untick anything you don't want in the spreadsheets.</div>`
          : `<div class="hint">Options appear here after the first scan - for now, everything the site posts is included.</div>`}
      </div>
      <div class="field">
        <label>Location pages</label>
        <div id="locations-list" class="stack" style="--sp-4: 8px;"></div>
      </div>
      <div class="row" style="justify-content:flex-end;">
        <button class="btn btn-secondary" data-close-modal>Close</button>
        <button class="btn btn-primary" id="save-filters">Save</button>
      </div>
    </div>`));

  const locList = body.querySelector("#locations-list");
  for (const site of company.sites.filter((s) => s.enabled)) {
    const row = el(`
      <div class="row between" style="border:1px solid var(--line);border-radius:8px;padding:8px 12px;">
        <div style="min-width:0;">
          <div class="small" style="font-weight:600;">${esc(site.label || new URL(site.url).pathname)}</div>
          <div class="small muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:320px;">${esc(site.url)}</div>
        </div>
        <div class="row" style="gap:6px;">
          <button class="btn btn-ghost btn-repoint" data-tip="Point at a newer page">Re-point</button>
          <button class="btn btn-ghost btn-discover" data-tip="Search this site for other bid pages">Find pages</button>
          <button class="btn btn-ghost btn-del" data-tip="Stop watching this page">✕</button>
        </div>
      </div>`);
    row.querySelector(".btn-repoint").addEventListener("click", async () => {
      const url = prompt("Paste the newer page's address:", site.url);
      if (!url || url === site.url) return;
      try {
        await api(`/api/sites/${site.id}/repoint`, { method: "POST", body: JSON.stringify({ url }) });
        toast("Page updated. The next scan reads the new page.");
        closeModals(); refresh();
      } catch (e) { toast(e.message); }
    });
    row.querySelector(".btn-discover").addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true; btn.textContent = "Searching…";
      try {
        const found = await api(`/api/sites/${site.id}/discover`, { method: "POST" });
        btn.textContent = "Find pages"; btn.disabled = false;
        if (!found.candidates.length) { toast("No other bid pages spotted on this site."); return; }
        const list = el(`<div class="stack" style="--sp-4:6px;margin-top:6px;"></div>`);
        for (const cand of found.candidates.slice(0, 6)) {
          const item = el(`<div class="row between" style="background:var(--surface-2);border-radius:6px;padding:6px 10px;">
            <span class="small" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:340px;">${esc(cand.url)}</span>
            <button class="btn btn-secondary" style="padding:2px 10px;">Watch it</button></div>`);
          item.querySelector("button").addEventListener("click", async () => {
            try {
              await api(`/api/links/${cand.id}/accept`, { method: "POST" });
              toast("Added. It'll be included in the next scan.");
              item.remove(); closeModals(); refresh();
            } catch (e) { toast(e.message); }
          });
          list.appendChild(item);
        }
        row.after(list);
      } catch (e) {
        btn.textContent = "Find pages"; btn.disabled = false;
        toast(e.message);
      }
    });
    row.querySelector(".btn-del").addEventListener("click", async () => {
      if (!confirm("Stop watching this page?")) return;
      try {
        await api(`/api/sites/${site.id}`, { method: "DELETE" });
        row.remove(); refresh();
      } catch (e) { toast(e.message); }
    });
    locList.appendChild(row);
  }

  body.querySelector("#save-filters").addEventListener("click", async () => {
    const checks = body.querySelectorAll("#commodity-checks input");
    const picked = [...checks].filter((c) => c.checked).map((c) => c.value);
    const override = {};
    if (checks.length && picked.length && picked.length < checks.length) {
      override.commodities = picked;
    } else if (checks.length) {
      override.commodities = [];
    }
    try {
      await api(`/api/companies/${company.id}/filters`, {
        method: "PUT",
        body: JSON.stringify(override),
      });
      toast("Saved. These choices stick until you change them.");
      closeModals();
    } catch (e) {
      toast(e.message);
    }
  });
}

/* ---------- boot ---------- */
refresh();
