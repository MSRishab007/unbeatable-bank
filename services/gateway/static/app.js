/* Unbeatable Bank web app: customer banking and the staff console.
   Plain JavaScript, no build step. Everything goes through the gateway's /api. */
"use strict";

(() => {
  const $app = document.getElementById("app");

  // ------------------------------------------------------------------ storage

  let memorySession = null;
  const store = {
    get session() {
      try { return JSON.parse(sessionStorage.getItem("ub.session")) || memorySession; } catch { return memorySession; }
    },
    set session(value) {
      memorySession = value;
      try {
        if (value) sessionStorage.setItem("ub.session", JSON.stringify(value));
        else sessionStorage.removeItem("ub.session");
      } catch { /* private mode: the in-memory copy is enough */ }
    },
  };

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const x = [...b].map((n) => n.toString(16).padStart(2, "0")).join("");
    return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
  }

  // A stable id for this browser, so the bank can tell a known device from a new one.
  function deviceFingerprint() {
    try {
      let id = localStorage.getItem("ub.device");
      if (!id) { id = "web-" + uuid(); localStorage.setItem("ub.device", id); }
      return id;
    } catch { return (deviceFingerprint.memo ||= "web-" + uuid()); }
  }

  function deviceLabel() {
    const ua = navigator.userAgent;
    const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox"
      : /Safari\//.test(ua) ? "Safari" : "Browser";
    const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Windows/.test(ua) ? "Windows"
      : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "device";
    return `${browser} on ${os}`;
  }

  // ------------------------------------------------------------------ DOM and formatting

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    const a = attrs || {};
    for (const [k, v] of Object.entries(a)) {
      if (v == null || v === false || k === "value") continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    if (a.value != null) el.value = a.value;   // after the options exist, for <select>
    return el;
  }

  const rupees = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const inr = (paise) => (paise < 0 ? "−₹" : "₹") + rupees.format(Math.abs(paise || 0) / 100);
  const pct = (rate) => (rate * 100).toFixed(2).replace(/\.?0+$/, (m) => (m.startsWith(".") ? "" : m)) + "%";
  const toPaise = (text) => {
    const n = Number(String(text).replace(/[,\s₹]/g, ""));
    return Number.isFinite(n) && n > 0 ? Math.round(n * 100) : 0;
  };
  const timeFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  const dayFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });
  const asDate = (v) => (typeof v === "number" ? new Date(v * 1000) : new Date(v));
  const when = (v) => (v == null ? "" : timeFmt.format(asDate(v)));
  const day = (v) => (v == null ? "" : dayFmt.format(new Date(String(v).length === 10 ? v + "T00:00:00" : v)));
  const words = (code) => String(code || "").toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const short = (id) => String(id || "").slice(0, 8);

  function toast(message, bad) {
    const el = h("div", { class: "toast" + (bad ? " bad" : "") }, message);
    document.getElementById("toasts").append(el);
    setTimeout(() => el.remove(), bad ? 7000 : 4000);
  }

  function tag(text, tone) { return h("span", { class: "tag " + (tone || "") }, text); }

  const STATUS_TONE = {
    COMPLETED: "good", ACTIVE: "good", VERIFIED: "good", RESOLVED: "good", APPROVED: "good", MATURED: "good", OPEN: "warn",
    CHALLENGE: "warn", HELD: "warn", PROCESSING: "warn", PENDING: "warn", PENDING_KYC: "warn", MEDIUM: "warn", LOW: "",
    DECLINED: "bad", FROZEN: "bad", REJECTED: "bad", FAILED: "bad", HIGH: "bad", CRITICAL: "bad", CLOSED: "", CANCELLED: "",
  };
  const statusTag = (s) => tag(words(s), STATUS_TONE[s] || "");

  async function copy(text, what) {
    try { await navigator.clipboard.writeText(text); toast(`${what || "Copied"} copied`); }
    catch { window.prompt("Copy this:", text); }
  }
  const copyBtn = (text, what) => h("button", { class: "link small", type: "button", onclick: () => copy(text, what) }, "Copy");

  // Runs an async action from a button: disables it meanwhile and shows any error.
  async function busy(button, fn) {
    if (button.disabled) return;
    button.disabled = true;
    try { await fn(); } catch (e) { toast(e.message || "Something went wrong.", true); } finally { button.disabled = false; }
  }

  // ------------------------------------------------------------------ API

  class ApiError extends Error {
    constructor(status, message, data) { super(message); this.status = status; this.data = data; }
  }

  function messageOf(data, status) {
    if (data && typeof data === "object") {
      if (typeof data.message === "string") return data.message;
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail)) {
        return data.detail.map((d) => `${words(String(d.loc?.slice(-1)[0] ?? "input"))}: ${d.msg}`).join(". ");
      }
    }
    if (status === 429) return "Too many attempts. Wait a minute and try again.";
    if (status === 403) return "You don't have access to that.";
    if (status === 404) return "Not found.";
    if (status >= 500) return "The bank couldn't finish that just now. Nothing was changed; try again.";
    return `Request failed (${status}).`;
  }

  let refreshing = null;
  function refreshSession() {
    refreshing ||= (async () => {
      const s = store.session;
      try {
        const res = await fetch("/api/auth/refresh", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: s.refresh }),
        });
        if (!res.ok) return false;
        const data = await res.json();
        store.session = { ...s, access: data.access_token, refresh: data.refresh_token || s.refresh };
        return true;
      } catch { return false; } finally { setTimeout(() => { refreshing = null; }, 0); }
    })();
    return refreshing;
  }

  // opt.accept: extra status codes to hand back instead of throwing; opt.full: return {status, data};
  // opt.blob: return the response as a file. A Blob body is uploaded as the file itself.
  async function api(method, path, body, opt = {}) {
    const s = store.session;
    const headers = { ...(opt.headers || {}) };
    const file = body instanceof Blob;                    // a file is sent as it is, not as JSON
    if (file) headers["Content-Type"] = body.type || "application/octet-stream";
    else if (body !== undefined) headers["Content-Type"] = "application/json";
    if (s && !opt.anonymous) headers.Authorization = "Bearer " + s.access;
    let res;
    try {
      res = await fetch(path, { method, headers, body: file ? body : body !== undefined ? JSON.stringify(body) : undefined });
    } catch {
      throw new ApiError(0, "Can't reach the bank. Check your connection and try again.");
    }
    if (res.status === 401 && s && !opt.anonymous) {
      if (!opt.retried && s.kind === "customer" && s.refresh && await refreshSession()) {
        return api(method, path, body, { ...opt, retried: true });
      }
      signOut(true);
      throw new ApiError(401, "Your session ended. Sign in again.");
    }
    if (opt.blob && res.ok) return res.blob();
    const text = await res.text();
    let data = null;
    if (text) { try { data = JSON.parse(text); } catch { data = text; } }
    if (!res.ok && !(opt.accept || []).includes(res.status)) throw new ApiError(res.status, messageOf(data, res.status), data);
    return opt.full ? { status: res.status, data } : data;
  }
  const get = (path) => api("GET", path);

  // ------------------------------------------------------------------ dialogs

  function dialog(build) {
    const d = h("dialog");
    const close = () => d.close();
    d.addEventListener("close", () => { d.dispatchEvent(new Event("gone")); d.remove(); });
    d.addEventListener("click", (e) => { if (e.target === d) close(); });
    d.append(build(close, d));
    document.body.append(d);
    d.showModal();
    return d;
  }

  // A dialog with a form. onSubmit gets the fields as an object; throw to show an error and stay open.
  function formDialog({ title, lead, fields, submit, danger, onSubmit }) {
    return dialog((close) => {
      const error = h("p", { class: "help bad", role: "alert" });
      const go = h("button", { type: "submit", class: danger ? "danger" : "" }, submit);
      const form = h("form", { novalidate: false },
        h("h2", null, title), lead && h("p", { class: "lead" }, lead), fields,
        h("div", { class: "field" }, error),
        h("div", { class: "actions" }, h("button", { type: "button", class: "quiet", onclick: close }, "Cancel"), go));
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (go.disabled) return;
        go.disabled = true; error.textContent = "";
        try {
          const keep = await onSubmit(Object.fromEntries(new FormData(form)));
          if (keep !== "keep") close();
        } catch (err) { error.textContent = err.message || "Something went wrong."; } finally { go.disabled = false; }
      });
      return form;
    });
  }

  function field(label, input, help) {
    const control = input.matches("input,select,textarea") ? input : input.querySelector("input,select,textarea") || input;
    const id = control.id || (control.id = "f-" + Math.random().toString(36).slice(2, 9));
    return h("div", { class: "field" }, h("label", { for: id }, label), input,
      help && (help.nodeType ? help : h("p", { class: "help" }, help)));
  }
  const amountInput = (name, value, extra) => h("div", { class: "amount-input" },
    h("input", { name, inputmode: "decimal", autocomplete: "off", required: true, placeholder: "0.00", value, ...(extra || {}) }));

  // Private files (KYC documents, statements) need the access token, so they are fetched and
  // shown from memory rather than linked to.
  async function viewFile(path, title, filename) {
    const blob = await api("GET", path, undefined, { blob: true });
    const url = URL.createObjectURL(blob);
    const d = dialog((close) => h("div", { class: "body" }, h("h2", null, title),
      blob.type.startsWith("image/")
        ? h("img", { src: url, alt: title, class: "file-view" })
        : h("iframe", { src: url, title, class: "file-view pdf" }),
      h("div", { class: "actions", style: "margin-top:14px" },
        h("a", { class: "btn quiet", href: url, download: filename || title.toLowerCase().replace(/[^a-z0-9]+/g, "-") + (blob.type === "application/pdf" ? ".pdf" : "") }, "Download"),
        h("button", { type: "button", onclick: close }, "Close"))));
    d.classList.add("wide");
    d.addEventListener("gone", () => URL.revokeObjectURL(url));
  }

  // Monthly statements for an account, plus a statement for any date range.
  function statementsPanel(account, listing) {
    const base = `/api/accounts/${account.account_id}/statements`;
    const last4 = account.account_number.slice(-4);
    const rows = listing.periods.map((p) => {
      const li = h("li", { "data-statement": p.period },
        h("div", { class: "main" }, h("strong", null, p.label),
          h("span", { class: "muted small" }, p.issued ? `${p.entry_count} entr${p.entry_count === 1 ? "y" : "ies"}, closing balance ${inr(p.closing_balance)}` : "Not downloaded yet")),
        h("div", { class: "actions" },
          h("button", { class: "quiet small", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
            const r = await get(`${base}/${p.period}/verify`).catch((err) => { if (err.status === 404) throw new Error("Open the statement once first; then it can be checked."); throw err; });
            toast(r.valid ? `${p.label}: matches the ledger (${r.entry_count} entries) and the file is unchanged`
              : r.ledger_matches ? `${p.label}: the stored file has been changed` : `${p.label}: the ledger no longer matches this statement`, !r.valid);
          }) }, "Check"),
          h("button", { class: "small", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
            await viewFile(`${base}/${p.period}`, `Statement for ${p.label}`, `statement-${last4}-${p.period}.pdf`);
            li.querySelector(".muted").textContent = "Downloaded";
          }) }, "Open")));
      return li;
    });
    const monthStart = listing.business_date.slice(0, 8) + "01";
    const from = h("input", { type: "date", name: "date_from", value: monthStart > listing.opened_on ? monthStart : listing.opened_on, max: listing.business_date, required: true });
    const to = h("input", { type: "date", name: "date_to", value: listing.business_date, max: listing.business_date, required: true });
    const go = h("button", { class: "quiet", type: "submit" }, "Get statement");
    const form = h("form", { class: "sheet", id: "interim-form", style: "margin-top:16px" },
      h("h3", { style: "margin-bottom:12px" }, "Any dates"),
      h("div", { class: "row" }, field("From", from), field("To", to)), go,
      h("p", { class: "help" }, `Up to one year, ending no later than the bank's date (${day(listing.business_date)}).`));
    form.addEventListener("submit", (e) => { e.preventDefault(); busy(go, () =>
      viewFile(`${base}/interim?date_from=${from.value}&date_to=${to.value}`, `Statement ${day(from.value)} to ${day(to.value)}`, `statement-${last4}-${from.value}-to-${to.value}.pdf`)); });
    rows.slice(6).forEach((li) => { li.hidden = true; });
    const more = rows.length > 6 && h("button", { class: "link", type: "button", id: "more-statements", style: "margin-top:10px", onclick: (e) => {
      rows.forEach((li) => { li.hidden = false; }); e.currentTarget.remove();
    } }, `Show ${rows.length - 6} earlier month${rows.length - 6 > 1 ? "s" : ""}`);
    return h("div", { id: "statements" },
      rows.length ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, rows))
        : empty("No monthly statements yet", "The first one is ready when this month ends."),
      more, form);
  }

  // Anyone holding a statement PDF can ask whether the bank really issued it. No login needed.
  function checkStatementDialog() {
    dialog((close) => {
      const out = h("div", { role: "status", id: "statement-check-result", style: "margin-top:14px" });
      const file = h("input", { type: "file", accept: "application/pdf,.pdf", id: "statement-file", "aria-label": "Statement PDF" });
      file.addEventListener("change", async () => {
        const f = file.files[0];
        if (!f) return;
        out.replaceChildren(h("p", { class: "muted" }, "Checking…"));
        try {
          const r = await api("POST", "/api/statements/verify", f, { anonymous: true });
          out.replaceChildren(r.genuine
            ? h("div", { class: "notice good" }, h("strong", null, "Genuine: Unbeatable Bank issued exactly this file"),
              `Account ${r.account_number}, ${day(r.date_from)} to ${day(r.date_to)}, closing balance ${inr(r.closing_balance)}, issued ${day(r.issued_on)}.`,
              r.matches_ledger_now ? "" : " The bank's records for this period have changed since it was issued.")
            : h("div", { class: "notice bad" }, h("strong", null, "Not recognised"), r.message));
        } catch (e) { out.replaceChildren(h("div", { class: "notice bad" }, e.message)); }
      });
      return h("div", { class: "body" }, h("h2", null, "Check a statement"),
        h("p", { class: "lead" }, "Upload a statement PDF you were given. We compare it with the files the bank issued; a single changed digit is enough for it to fail."),
        field("Statement PDF", file), out,
        h("div", { class: "actions" }, h("button", { class: "quiet", type: "button", onclick: close }, "Close")));
    });
  }

  const KYC_FILE_TYPES = ["image/jpeg", "image/png", "application/pdf"];
  const KYC_HELP = {
    PAN: "A clear picture of your PAN card.",
    ADDRESS_PROOF: "Masked Aadhaar (only the last 4 digits showing), passport, voter ID or a utility bill.",
    PHOTO: "A recent photo of your face.",
  };

  // Customer: what the bank needs, what was sent, and what staff said about it.
  function kycPanel(status, onChange) {
    const rows = status.types.map((t) => {
      const doc = t.document;
      const input = h("input", { type: "file", accept: ".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf", hidden: true, "data-kyc-file": t.doc_type, "aria-label": `Choose a file for ${t.label}` });
      const pick = h("button", { class: doc?.status === "REJECTED" || !doc ? "small" : "small quiet", type: "button", onclick: () => input.click() },
        !doc ? "Upload" : doc.status === "REJECTED" ? "Upload again" : "Replace");
      input.addEventListener("change", () => busy(pick, async () => {
        const file = input.files[0];
        input.value = "";
        if (!file) return;
        if (!KYC_FILE_TYPES.includes(file.type)) throw new Error("Choose a JPEG or PNG picture, or a PDF.");
        if (file.size > status.max_bytes) throw new Error(`That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is ${status.max_bytes / 1048576} MB.`);
        await api("POST", `/api/kyc/documents?doc_type=${t.doc_type}`, file);
        toast(`${t.label[0].toUpperCase() + t.label.slice(1)} sent for review`);
        onChange();
      }));
      const label = t.label[0].toUpperCase() + t.label.slice(1);
      return h("li", { "data-kyc": t.doc_type },
        h("div", { class: "main" }, h("strong", null, label, t.required ? "" : " (optional)"),
          h("span", { class: "muted small" }, doc?.status === "REJECTED" ? h("span", { style: "color:var(--red)" }, `Not accepted: ${doc.reject_reason}`)
            : doc ? `Sent ${when(doc.uploaded_at)}` : KYC_HELP[t.doc_type])),
        h("div", { class: "actions" },
          doc ? tag({ PENDING: "Being reviewed", APPROVED: "Accepted", REJECTED: "Not accepted" }[doc.status], STATUS_TONE[doc.status]) : t.required && tag("Needed", "warn"),
          doc?.has_file && h("button", { class: "quiet small", type: "button", onclick: (e) => busy(e.currentTarget, () => viewFile(`/api/kyc/documents/${doc.document_id}/content`, label)) }, "View"),
          doc?.status !== "APPROVED" && [pick, input]));
    });
    return h("div", { class: "sheet flush", id: "kyc-documents" }, h("ul", { class: "list" }, rows));
  }

  // ------------------------------------------------------------------ routing and shell

  const state = { unread: 0, seenNotifications: null, poll: null, me: null, renderSeq: 0, next: null };

  function parseHash() {
    const raw = location.hash.replace(/^#/, "") || "/";
    const [path, qs] = raw.split("?");
    return { path, query: new URLSearchParams(qs || "") };
  }
  const go = (path) => { if (location.hash === "#" + path) render(); else location.hash = path; };

  function signOut(expired) {
    const s = store.session;
    if (s && !expired) {
      const path = s.kind === "customer" ? "/api/auth/logout" : null;
      if (path) fetch(path, { method: "POST", headers: { Authorization: "Bearer " + s.access } }).catch(() => {});
    }
    clearInterval(state.poll); state.poll = null; state.me = null; state.unread = 0; state.seenNotifications = null;
    store.session = null;
    if (expired) { state.next = location.hash; toast("Your session ended. Sign in again.", true); }
    render();
  }

  const CUSTOMER_NAV = [
    ["/accounts", "Accounts"], ["/pay", "Pay"], ["/deposits", "Deposits"], ["/inbox", "Inbox"], ["/security", "Security"],
  ];
  const STAFF_NAV = [
    ["/staff/fraud", "Fraud desk", ["ANALYST"]], ["/staff/approvals", "Approvals", ["ANALYST"]],
    ["/staff/branch", "Branch desk", null], ["/staff/system", "System", null],
  ];
  const can = (...roles) => { const r = store.session?.role; return r === "ADMIN" || roles.includes(r); };

  const CUSTOMER_ROUTES = [
    [/^\/accounts$/, viewAccounts], [/^\/accounts\/([^/]+)$/, viewAccount], [/^\/pay$/, viewPay],
    [/^\/receive$/, viewReceive], [/^\/deposits$/, viewDeposits], [/^\/inbox$/, viewInbox], [/^\/security$/, viewSecurity],
  ];
  const STAFF_ROUTES = [
    [/^\/staff\/fraud$/, viewFraudDesk], [/^\/staff\/cases\/([^/]+)$/, viewCase], [/^\/staff\/approvals$/, viewApprovals],
    [/^\/staff\/branch$/, viewBranch], [/^\/staff\/system$/, viewSystem],
  ];

  function shell(s, path, content) {
    const nav = s.kind === "customer" ? CUSTOMER_NAV : STAFF_NAV.filter(([, , roles]) => !roles || can(...roles));
    const current = (href) => path === href || path.startsWith(href + "/")
      || (href === "/pay" && path === "/receive") || (href === "/staff/fraud" && path.startsWith("/staff/cases"));
    const rail = h("nav", { class: "rail", "aria-label": "Main" },
      h("div", { class: "brand" }, "Unbeatable Bank", h("small", null, s.kind === "staff" ? "Staff console" : "Personal banking")),
      nav.map(([href, label]) => h("a", { href: "#" + href, "aria-current": current(href) ? "page" : null }, label,
        href === "/inbox" && h("span", { class: "badge", id: "unread", hidden: !state.unread }, state.unread))),
      h("div", { class: "spacer" }),
      h("div", { class: "who" }, h("strong", null, s.name), s.kind === "staff" ? words(s.role) : s.email),
      h("a", { href: "#", class: "desktop-only", onclick: (e) => { e.preventDefault(); signOut(); } }, "Sign out"));
    const top = h("div", { class: "mobile-top" }, "Unbeatable Bank",
      h("button", { class: "quiet small", type: "button", onclick: () => signOut() }, "Sign out"));
    return h("div", { class: "shell" }, rail, h("main", { id: "main" }, top, content));
  }

  async function render() {
    const s = store.session;
    const { path, query } = parseHash();
    const seq = ++state.renderSeq;
    document.querySelectorAll("dialog[open]").forEach((d) => d.close());
    if (!s) { $app.replaceChildren(viewGate(path.startsWith("/staff") ? "staff" : query.get("mode") || "signin")); return; }

    const routes = s.kind === "customer" ? CUSTOMER_ROUTES : STAFF_ROUTES;
    let match = null;
    for (const [re, view] of routes) { const m = path.match(re); if (m) { match = [view, m.slice(1)]; break; } }
    if (!match) {
      const home = s.kind === "customer" ? "/accounts" : (can("ANALYST") ? "/staff/fraud" : "/staff/branch");
      location.replace("#" + home);
      return;
    }
    const holder = h("div", null, h("p", { class: "skeleton" }, "Loading…"));
    $app.replaceChildren(shell(s, path, holder));
    window.scrollTo(0, 0);
    if (s.kind === "customer") startPolling();
    try {
      const node = await match[0](...match[1], query);
      if (seq === state.renderSeq) holder.replaceChildren(node);
    } catch (e) {
      if (seq !== state.renderSeq || !store.session) return;
      holder.replaceChildren(h("div", { class: "notice bad" }, h("strong", null, "This page didn't load"), e.message,
        h("div", { class: "actions", style: "margin-top:12px" }, h("button", { class: "small", onclick: render }, "Try again"))));
    }
  }
  window.addEventListener("hashchange", render);

  function page(title, lead, actions, ...sections) {
    document.title = `${title} | Unbeatable Bank`;
    return h("div", null,
      h("div", { class: "page-head" }, h("div", null, h("h1", null, title), lead && h("p", null, lead)),
        actions && h("div", { class: "actions" }, actions)),
      sections);
  }
  const section = (title, action, ...body) => h("section", null,
    h("div", { class: "section-head" }, h("h2", null, title), action), body);
  const empty = (title, text, action) => h("div", { class: "sheet empty" }, h("strong", null, title), text,
    action && h("div", { class: "actions", style: "justify-content:center;margin-top:14px" }, action));

  // ------------------------------------------------------------------ inbox polling

  async function pollInbox() {
    if (store.session?.kind !== "customer") return;
    let items;
    try { items = await get("/api/notifications?limit=50"); } catch { return; }
    const fresh = state.seenNotifications ? items.filter((n) => !state.seenNotifications.has(n.id)) : [];
    state.seenNotifications = new Set(items.map((n) => n.id));
    state.unread = items.filter((n) => !n.read).length;
    const badge = document.getElementById("unread");
    if (badge) { badge.textContent = state.unread; badge.hidden = !state.unread; }
    if (fresh.length && parseHash().path !== "/inbox") toast(`Inbox: ${fresh[0].title}`);
    return items;
  }
  function startPolling() {
    if (state.poll) return;
    pollInbox();
    state.poll = setInterval(pollInbox, 12000);
  }

  // ------------------------------------------------------------------ sign-in and sign-up

  function viewGate(mode) {
    document.title = "Unbeatable Bank";
    const error = h("p", { class: "help bad", role: "alert" });
    const submitting = async (button, fn) => {
      error.textContent = ""; button.disabled = true;
      try { await fn(); } catch (e) { error.textContent = e.message; } finally { button.disabled = false; }
    };
    const enter = () => { const next = state.next; state.next = null; if (next && next !== "#/") location.hash = next; render(); };
    if (!state.next && /^#\/(pay|accounts|deposits|inbox|security|receive)/.test(location.hash)) state.next = location.hash;

    async function signIn(identifier, password) {
      const t = await api("POST", "/api/auth/login",
        { identifier, password, device_fingerprint: deviceFingerprint(), device_label: deviceLabel() }, { anonymous: true });
      store.session = { kind: "customer", access: t.access_token, refresh: t.refresh_token, name: "Customer", email: identifier };
      try {
        const me = await get("/api/me");
        store.session = { ...store.session, name: me.full_name, email: me.email };
      } catch { /* the profile loads again on the home page */ }
      if (t.new_device) toast("New device: we've noted this sign-in in your security log.");
      enter();
    }

    const forms = {
      signin: () => {
        const btn = h("button", { type: "submit" }, "Sign in");
        const form = h("form", null,
          field("Email or mobile number", h("input", { name: "identifier", autocomplete: "username", required: true })),
          field("Password", h("input", { name: "password", type: "password", autocomplete: "current-password", required: true })),
          h("div", { class: "field" }, error), btn);
        form.addEventListener("submit", (e) => {
          e.preventDefault();
          const v = Object.fromEntries(new FormData(form));
          const id = /^[6-9]\d{9}$/.test(v.identifier.trim()) ? "+91" + v.identifier.trim() : v.identifier.trim().toLowerCase();
          submitting(btn, () => signIn(id, v.password));
        });
        return [h("h2", null, "Sign in"), h("p", { class: "muted" }, "Use the email or mobile number you registered with."), form];
      },
      register: () => {
        const btn = h("button", { type: "submit" }, "Create my login");
        const form = h("form", null,
          field("Full name", h("input", { name: "full_name", autocomplete: "name", required: true, minlength: 2 })),
          h("div", { class: "row" },
            field("Date of birth", h("input", { name: "date_of_birth", type: "date", required: true, max: new Date().toISOString().slice(0, 10) })),
            field("Mobile number", h("input", { name: "phone", inputmode: "numeric", autocomplete: "tel-national", required: true,
              pattern: "[6-9][0-9]{9}", placeholder: "10 digits", title: "10-digit Indian mobile number" }))),
          field("Email", h("input", { name: "email", type: "email", autocomplete: "email", required: true })),
          field(h("span", null, "PAN ", h("span", { class: "hint" }, "(optional)")), h("input", { name: "pan", pattern: "[A-Za-z]{5}[0-9]{4}[A-Za-z]",
            placeholder: "ABCDE1234F", autocomplete: "off", title: "5 letters, 4 digits, 1 letter" }),
            "We keep only a one-way hash and the last five characters."),
          field("Password", h("input", { name: "password", type: "password", autocomplete: "new-password", required: true, minlength: 8 }),
            "At least 8 characters."),
          h("div", { class: "field" }, error), btn);
        form.addEventListener("submit", (e) => {
          e.preventDefault();
          const v = Object.fromEntries(new FormData(form));
          const body = { full_name: v.full_name.trim(), date_of_birth: v.date_of_birth, phone: "+91" + v.phone.trim(),
            email: v.email.trim().toLowerCase(), password: v.password };
          if (v.pan) body.pan = v.pan.toUpperCase();
          submitting(btn, async () => {
            await api("POST", "/api/auth/register", body, { anonymous: true });
            await signIn(body.email, body.password);
          });
        });
        return [h("h2", null, "Open your account"),
          h("p", { class: "muted" }, "Create a login first. A branch officer verifies your KYC before money can move."), form];
      },
      staff: () => {
        const btn = h("button", { type: "submit" }, "Sign in to the staff console");
        const form = h("form", null,
          field("Staff ID", h("input", { name: "staff_id", autocomplete: "username", required: true })),
          field("Password", h("input", { name: "password", type: "password", autocomplete: "current-password", required: true })),
          h("div", { class: "field" }, error), btn);
        form.addEventListener("submit", (e) => {
          e.preventDefault();
          const v = Object.fromEntries(new FormData(form));
          submitting(btn, async () => {
            const t = await api("POST", "/api/staff/login", { staff_id: v.staff_id.trim(), password: v.password }, { anonymous: true });
            store.session = { kind: "staff", access: t.access_token, name: t.staff_id, role: t.role };
            state.next = null;
            location.hash = "/staff";
            render();
          });
        });
        return [h("h2", null, "Staff console"), h("p", { class: "muted" }, "For fraud analysts, tellers, KYC officers and administrators."), form];
      },
    };
    if (!forms[mode]) mode = "signin";
    const tabs = h("div", { class: "seg", role: "group", "aria-label": "Choose" },
      [["signin", "Sign in"], ["register", "Open an account"], ["staff", "Staff"]].map(([key, label]) =>
        h("button", { type: "button", "aria-pressed": String(key === mode), onclick: () => $app.replaceChildren(viewGate(key)) }, label)));

    return h("div", { class: "gate" },
      h("div", { class: "pitch" },
        h("div", null, h("div", { class: "brand", style: "padding:0 0 40px" }, "Unbeatable Bank"),
          h("h1", null, "Every payment passes five checks before a rupee moves."),
          h("p", null, "Savings and current accounts, fixed and recurring deposits, scan-to-pay, and a fraud desk that watches every transaction as it happens.")),
        h("ul", { class: "ledger-lines" },
          [["Duplicate check", "One reference, one payment"], ["Rate limits", "Shared by every server"],
            ["Risk engine", "Scores device, payee and pattern"], ["Double-entry ledger", "Debits always equal credits"],
            ["Stream analytics", "Finds rings and mule accounts"]].map(([a, b]) => h("li", null, h("span", null, a), h("span", null, b))))),
      h("div", { class: "form" }, h("div", null, tabs), forms[mode](),
        h("p", { class: "muted small", style: "margin-top:28px" }, "Been given an Unbeatable Bank statement? ",
          h("button", { class: "link", type: "button", onclick: checkStatementDialog }, "Check it is genuine"))));
  }

  // ------------------------------------------------------------------ customer: accounts

  async function loadMe() {
    state.me = await get("/api/me");
    const s = store.session;
    if (s && s.name !== state.me.full_name) store.session = { ...s, name: state.me.full_name, email: state.me.email };
    return state.me;
  }
  const PRODUCT = { SAVINGS: "Savings", CURRENT: "Current", FD: "Fixed deposit", RD: "Recurring deposit" };
  const payable = (a) => ["SAVINGS", "CURRENT"].includes(a.product_type);
  const groupDigits = (n) => String(n).replace(/(\d{4})(?=\d)/g, "$1 ");

  function kycNotice(me) {
    if (me.kyc_status === "VERIFIED") return null;
    return h("div", { class: "notice" }, h("strong", null, me.kyc_status === "REJECTED" ? "Your KYC was not accepted" : "Your KYC is waiting for the branch"),
      "You can open accounts now; they become active once your documents are accepted. ",
      h("a", { href: "#/security" }, "Upload your documents"), ", or visit a branch and quote customer number ",
      h("b", { class: "num" }, me.cif_number), ".");
  }

  function openAccountDialog() {
    formDialog({
      title: "Open an account", submit: "Open account",
      lead: "It gets its own account number straight away. You can hold as many accounts as you like.",
      fields: [
        field("Account type", h("select", { name: "product_type" },
          h("option", { value: "SAVINGS" }, "Savings: earns interest on the daily balance"),
          h("option", { value: "CURRENT" }, "Current: for business use, can carry an overdraft"))),
        field(h("span", null, "Joint holder ", h("span", { class: "hint" }, "(optional)")),
          h("input", { name: "joint", placeholder: "Their customer ID", autocomplete: "off" }),
          "Ask them for the customer ID shown on their Security page. Either holder can operate the account."),
      ],
      onSubmit: async (v) => {
        const body = { product_type: v.product_type };
        if (v.joint.trim()) body.joint_holder_ids = [v.joint.trim()];
        const a = await api("POST", "/api/accounts", body);
        toast(a.status === "ACTIVE" ? `Account ${a.account_number} is open` : `Account ${a.account_number} opens once KYC is verified`);
        go("/accounts/" + a.account_id);
      },
    });
  }

  function cover(a) {
    const frozen = a.status === "FROZEN";
    return h("a", { class: `cover ${a.product_type === "CURRENT" ? "current" : ""} ${frozen ? "frozen" : ""}`, href: "#/accounts/" + a.account_id },
      h("div", { class: "kind" }, h("span", null, PRODUCT[a.product_type] || a.product_type, a.my_role === "JOINT" ? " (joint)" : ""),
        a.status !== "ACTIVE" && h("span", { class: "tag" }, words(a.status))),
      h("div", { class: "balance" }, a.available_balance == null ? "Balance unavailable" : inr(a.available_balance)),
      h("div", { class: "sub" }, a.available_balance != null && a.ledger_balance !== a.available_balance
        ? `${inr(a.ledger_balance - a.available_balance)} on hold` : "Available now"),
      h("div", { class: "acct" }, groupDigits(a.account_number)));
  }

  function paymentRow(p, onOpen) {
    const sign = p.status === "COMPLETED" ? "−" : "";
    return h("li", null,
      h("div", { class: "main" }, h("strong", null, p.to_display_name || p.payee_ref || "Payment"),
        h("span", { class: "muted small" }, when(p.created_at), p.note ? `, “${p.note}”` : "")),
      h("div", { class: "actions" }, statusTag(p.status), h("b", { class: "num" }, sign + inr(p.amount).replace("−", "")),
        h("button", { class: "quiet small", type: "button", onclick: () => onOpen(p) }, "Details")));
  }

  async function viewAccounts() {
    const [me, accounts, payments] = await Promise.all([loadMe(), get("/api/me/accounts"), get("/api/payments?limit=6")]);
    const everyday = accounts.filter(payable);
    const first = me.full_name.split(" ")[0];
    return page(`Namaste, ${first}`, null,
      [h("a", { class: "btn", href: "#/pay" }, "Pay someone"), h("a", { class: "btn quiet", href: "#/receive" }, "Receive by QR")],
      kycNotice(me),
      section("Your accounts", null,
        h("div", { class: "covers" }, everyday.map(cover),
          h("button", { class: "cover add", type: "button", onclick: openAccountDialog }, everyday.length ? "Open another account" : "Open your first account"))),
      section("Recent payments", payments.length ? h("a", { href: "#/pay" }, "All payments") : null,
        payments.length
          ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, payments.map((p) => paymentRow(p, openPayment))))
          : empty("No payments yet", "Money you send shows up here with the checks it went through.")));
  }

  async function viewAccount(accountId) {
    const [a, bal, entries, statements] = await Promise.all([
      get(`/api/accounts/${accountId}`), get(`/api/accounts/${accountId}/balance`), get(`/api/accounts/${accountId}/transactions?limit=100`),
      get(`/api/accounts/${accountId}/statements`).catch(() => null),
    ]);
    const active = a.status === "ACTIVE";
    const actions = payable(a) ? [
      active && h("a", { class: "btn", href: `#/pay?from=${a.account_id}` }, "Pay from this account"),
      active && h("a", { class: "btn quiet", href: `#/receive?account=${a.account_id}` }, "Receive by QR"),
    ] : null;

    const manage = payable(a) && h("div", { class: "actions" },
      h("button", { class: "quiet small", type: "button", onclick: () => formDialog({
        title: "Choose a pay ID", submit: "Save pay ID",
        lead: "People can pay you with this instead of your account number.",
        fields: field("Pay ID", h("div", { class: "inline" }, h("input", { name: "alias", required: true, minlength: 3, maxlength: 30,
          pattern: "[A-Za-z0-9._\\-]+", autocomplete: "off", placeholder: "yourname" }), h("span", { style: "align-self:center", class: "muted" }, "@unbeatable")),
        "Letters, numbers, dots and dashes."),
        onSubmit: async (v) => { const r = await api("POST", `/api/accounts/${a.account_id}/alias`, { alias: v.alias.trim() }); toast(`Your pay ID is ${r.alias}`); copy(r.alias, "Pay ID"); },
      }) }, "Set a pay ID"),
      h("button", { class: "quiet small", type: "button", onclick: () => formDialog({
        title: "Payment limits", submit: "Save limits",
        lead: "Lower limits shrink what a thief could take. You can raise them again up to the bank's maximum.",
        fields: h("div", { class: "row" },
          field("Per payment", amountInput("per_txn", (a.per_txn_limit / 100).toFixed(0))),
          field("Per day", amountInput("daily", (a.daily_limit / 100).toFixed(0)))),
        onSubmit: async (v) => {
          const per = toPaise(v.per_txn), daily = toPaise(v.daily);
          if (!per || !daily) throw new Error("Enter both limits in rupees.");
          await api("PATCH", `/api/accounts/${a.account_id}/limits`, { per_txn_limit: per, daily_limit: daily });
          toast("Limits saved"); render();
        },
      }) }, "Change limits"),
      h("button", { class: "quiet small", type: "button", onclick: () => formDialog({
        title: "Add a joint holder", submit: "Add holder",
        lead: "They get full access to this account. Either of you can operate it.",
        fields: field("Their customer ID", h("input", { name: "customer_id", required: true, autocomplete: "off" }), "It's on their Security page."),
        onSubmit: async (v) => { await api("POST", `/api/accounts/${a.account_id}/holders`, { customer_id: v.customer_id.trim() }); toast("Joint holder added"); render(); },
      }) }, "Add a joint holder"),
      active && h("button", { class: "quiet small danger", type: "button", onclick: () => formDialog({
        title: "Freeze this account", submit: "Freeze account", danger: true,
        lead: "No money can leave while it's frozen. Unfreezing needs two bank officers, so only do this if something is wrong.",
        fields: field("What happened?", h("input", { name: "reason", required: true, minlength: 3, placeholder: "Lost phone, payment I don't recognise…" })),
        onSubmit: async (v) => { await api("POST", `/api/accounts/${a.account_id}/freeze`, { reason: v.reason }); toast("Account frozen"); render(); },
      }) }, "Freeze account"));

    const rows = entries.map((e) => h("tr", null,
      h("td", { class: "nowrap" }, when(e.created_at)),
      h("td", null, e.description || words(e.txn_type), h("div", { class: "muted small" }, words(e.txn_type))),
      h("td", { class: "right num debit" }, e.amount < 0 ? inr(-e.amount) : ""),
      h("td", { class: "right num credit" }, e.amount > 0 ? inr(e.amount) : ""),
      h("td", { class: "right num" }, inr(e.balance_after))));

    return page(`${PRODUCT[a.product_type] || a.product_type} account`, null, actions,
      a.status === "FROZEN" && h("div", { class: "notice bad" }, h("strong", null, "This account is frozen"),
        a.freeze_reason ? `Reason: ${a.freeze_reason}. ` : "", "Money can come in but not go out. Contact the branch to have it reviewed."),
      a.status === "PENDING_KYC" && h("div", { class: "notice" }, h("strong", null, "Waiting for KYC"),
        "This account becomes active when every holder's documents have been accepted. ",
        h("a", { href: "#/security" }, "Upload your documents")),
      h("div", { class: "grid-2" },
        h("div", { class: "sheet" }, h("dl", { class: "facts" },
          h("dt", null, "Account number"), h("dd", { class: "num" }, groupDigits(a.account_number), " ", copyBtn(a.account_number, "Account number")),
          h("dt", null, "Available"), h("dd", { class: "num", id: "available" }, inr(bal.available_balance)),
          bal.ledger_balance !== bal.available_balance && [h("dt", null, "On hold"), h("dd", { class: "num" }, inr(bal.ledger_balance - bal.available_balance))],
          a.overdraft_limit > 0 && [h("dt", null, "Overdraft"), h("dd", { class: "num" }, inr(a.overdraft_limit))],
          bal.accrued_interest > 0 && [h("dt", null, "Interest earned so far"), h("dd", { class: "num" }, inr(Math.floor(bal.accrued_interest)))],
          h("dt", null, "Status"), h("dd", null, statusTag(a.status)))),
        h("div", { class: "sheet" }, h("dl", { class: "facts" },
          h("dt", null, "Holders"), h("dd", null, a.holders.map((x) => `${x.full_name}${x.role === "PRIMARY" ? "" : " (joint)"}`).join(", ")),
          h("dt", null, "Per payment"), h("dd", { class: "num" }, inr(a.per_txn_limit)),
          h("dt", null, "Per day"), h("dd", { class: "num" }, inr(a.daily_limit)),
          h("dt", null, "Opened"), h("dd", null, day(a.opened_at))),
          manage && h("div", { style: "margin-top:16px" }, manage))),
      section("Passbook", null, rows.length
        ? h("div", { class: "sheet flush table-wrap" }, h("table", { class: "passbook" },
          h("thead", null, h("tr", null, h("th", null, "Date"), h("th", null, "Particulars"), h("th", { class: "right" }, "Withdrawal"),
            h("th", { class: "right" }, "Deposit"), h("th", { class: "right" }, "Balance"))),
          h("tbody", null, rows)))
        : empty("Nothing in the passbook yet", "Deposit cash at the branch or ask someone to pay you.",
          active && h("a", { class: "btn quiet", href: `#/receive?account=${a.account_id}` }, "Show my QR code"))),
      section("Statements", null, statements ? statementsPanel(a, statements)
        : h("div", { class: "notice" }, "Statements aren't available right now. Try again in a minute.")));
  }

  // ------------------------------------------------------------------ customer: payments

  // Which stage stopped a declined payment, judged from its decline code.
  function failedStage(code) {
    const c = String(code || "");
    if (/RATE_LIMIT|TOO_MANY/.test(c)) return 1;
    if (/LIMIT/.test(c)) return 3;
    if (/RISK|BLOCK|FRAUD|OTP|CHALLENGE|DENIED|REVIEW/.test(c)) return 4;
    if (/INSUFFICIENT|FUNDS|LEDGER/.test(c)) return 5;
    return 2;
  }

  function paymentChecks(p) {
    const names = ["Duplicate check", "Rate limits", "Account and payee", "Your limits", "Risk engine", "Ledger"];
    const notes = [
      p.replayed ? "We'd already seen this payment reference, so this is the original result, not a second payment."
        : "This payment has one reference. Sending it twice can't move money twice.",
      "Within the allowed number of payments a minute.",
      p.to_display_name ? `Paying ${p.to_display_name}.` : "Your account can pay and the payee can receive.",
      "Within your per-payment, per-day and new-payee limits.",
      p.risk_score == null ? "Scored on device, payee, amount and recent activity." : `Risk score ${p.risk_score} of 100: ${words(p.risk_decision)}.`,
      "Debited and credited in one transaction.",
    ];
    let stop = 6, stopKind = "fail";
    if (p.status === "DECLINED") stop = failedStage(p.decline_code);
    else if (p.status === "CHALLENGE" || p.status === "HELD") { stop = 4; stopKind = "wait"; }
    else if (p.status === "PROCESSING") { stop = 5; stopKind = "wait"; }
    if (stop === 4 && p.status === "DECLINED") notes[4] = p.risk_score == null ? "Stopped by the risk engine." : `Risk score ${p.risk_score} of 100: stopped.`;
    if (p.status === "HELD") notes[5] = "The money is set aside in the account until an analyst decides.";
    if (p.status === "DECLINED") notes[5] = "No money moved.";
    return h("ul", { class: "checks" }, names.map((name, i) => {
      const kind = i < stop ? "pass" : i === stop ? stopKind : (p.status === "HELD" && i === 5 ? "wait" : "skip");
      const mark = { pass: "✓", fail: "✕", wait: "…", skip: "–" }[kind];
      return h("li", { class: kind }, h("span", { class: "mark", "aria-hidden": "true" }, mark),
        h("span", null, h("b", null, name), kind === "skip" && p.status !== "DECLINED" ? " (not yet)" : ""),
        (kind !== "skip" || i === 5) && h("small", null, i === stop && kind === "fail" ? (p.decline_message || notes[i]) : notes[i]),
        i === 4 && p.risk_reasons?.length ? h("div", { class: "reasons" }, p.risk_reasons.map((r) => tag(words(r), kind === "pass" ? "" : "warn"))) : null);
    }));
  }

  const STAMP = {
    COMPLETED: ["Paid", "good"], DECLINED: ["Declined", "bad"], CHALLENGE: ["Confirm it's you", "warn"],
    HELD: ["Under review", "warn"], PROCESSING: ["Processing", "warn"], CANCELLED: ["Cancelled", "bad"],
  };

  // The payment as a slip: amount, stamp, the checks it went through, and what the customer can do next.
  function slip(p, { fresh, staff } = {}) {
    const box = h("div", { class: "slip", "data-status": p.status });
    const redraw = (next, isFresh) => box.replaceWith(slip(next, { fresh: isFresh, staff }));
    const [label, tone] = STAMP[p.status] || [words(p.status), ""];
    const headline = {
      COMPLETED: `Sent to ${p.to_display_name || p.payee_ref}.`,
      DECLINED: p.decline_message || "This payment was declined. No money moved.",
      CHALLENGE: "This looks different from your usual payments, so we sent a confirmation code to your inbox.",
      HELD: staff ? "Held for review. The money is set aside in the payer's account until the case is decided."
        : "We've paused this payment for a quick review. The money is set aside and hasn't left your account.",
    }[p.status] || p.message || "";

    let next = null;
    if (!staff && p.status === "CHALLENGE") {
      const input = h("input", { name: "otp", inputmode: "numeric", autocomplete: "one-time-code", required: true, minlength: 4, maxlength: 8, placeholder: "6-digit code", "aria-label": "Confirmation code" });
      const err = h("p", { class: "help bad", role: "alert" });
      const btn = h("button", { type: "submit" }, "Confirm payment");
      const form = h("form", { style: "margin-top:18px;max-width:340px" }, h("div", { class: "inline" }, input, btn), err,
        h("p", { class: "help" }, "The code is in your ", h("a", { href: "#/inbox" }, "inbox"), " and works for five minutes."));
      form.addEventListener("submit", async (e) => {
        e.preventDefault(); err.textContent = ""; btn.disabled = true;
        try {
          const r = await api("POST", `/api/payments/${p.txn_id}/confirm`, { otp: input.value.trim() }, { full: true, accept: [400, 401, 403, 409, 422] });
          if (r.data?.txn_id && r.data.status !== "CHALLENGE") redraw(r.data, true);
          else { err.textContent = r.data?.message || r.data?.decline_message || "That code isn't right. Check your inbox and try again."; input.select(); }
        } catch (ex) { err.textContent = ex.message; } finally { btn.disabled = false; }
      });
      next = form;
    } else if (!staff && p.status === "HELD") {
      next = h("div", { class: "actions", style: "margin-top:18px" },
        h("button", { class: "danger quiet", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
          const r = await api("POST", `/api/payments/${p.txn_id}/deny`, undefined, { accept: [422] });
          toast("Payment cancelled. We've protected your account."); redraw(r?.txn_id ? r : { ...p, status: "DECLINED", decline_message: "You told us this wasn't you. The payment was cancelled." }, true);
        }) }, "This wasn't me: cancel it"),
        h("span", { class: "muted small" }, "If you made this payment, there's nothing to do."));
    } else if (!staff && p.status === "COMPLETED") {
      next = h("div", { class: "actions", style: "margin-top:18px" },
        h("button", { class: "link", type: "button", onclick: () => formDialog({
          title: "Report this payment", submit: "Send report", danger: true,
          lead: "Tell us what's wrong. A fraud analyst will open a case and contact you.",
          fields: field("What happened?", h("textarea", { name: "description", required: true, minlength: 5, maxlength: 1000 })),
          onSubmit: async (v) => { const c = await api("POST", "/api/reports", { txn_id: p.txn_id, description: v.description }); toast(`Report received: ${c.case_number}`); },
        }) }, "Something wrong with this payment?"));
    }

    box.append(...[
      h("div", { class: "slip-head" },
        h("div", null, h("div", { class: "amount" }, inr(p.amount)),
          h("div", { class: "to" }, "to ", h("b", null, p.to_display_name || p.payee_ref), p.to_account_number ? ` (${groupDigits(p.to_account_number)})` : "",
            p.note ? `, “${p.note}”` : "")),
        h("div", { class: `stamp ${tone} ${fresh ? "fresh" : ""}` }, label)),
      h("p", { class: "message" }, headline), next, paymentChecks(p),
      h("p", { class: "muted small", style: "margin-top:14px" }, `Reference ${short(p.txn_id)}, ${when(p.created_at)}`, p.channel === "QR_PAYMENT" ? ", paid by QR" : "")].filter(Boolean));
    return box;
  }

  function openPayment(p, staff) {
    dialog((close) => h("div", { class: "body" }, slip(p, { staff }),
      h("div", { class: "actions", style: "margin-top:16px" }, h("button", { class: "quiet", type: "button", onclick: close }, "Close"))));
  }

  // "#/pay?to=...&amount=..." links are what our QR codes contain. Also accepts a bare account number or pay ID.
  function parsePayCode(text) {
    const t = String(text || "").trim();
    const m = t.match(/#\/pay\?(.*)$/);
    if (m) {
      const q = new URLSearchParams(m[1]);
      if (q.get("to")) return { to: q.get("to"), amount: q.get("amount") || "", note: q.get("note") || "" };
    }
    if (/^\d{12}$/.test(t.replace(/\s/g, ""))) return { to: t.replace(/\s/g, "") };
    if (/^[\w.\-]+@unbeatable$/i.test(t)) return { to: t.toLowerCase() };
    return null;
  }

  async function decodeQrFile(file) {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1400 / Math.max(bitmap.width, bitmap.height));
    const canvas = h("canvas", { width: Math.round(bitmap.width * scale), height: Math.round(bitmap.height * scale) });
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
    return window.jsQR(img.data, img.width, img.height)?.data || null;
  }

  function scanDialog(onCode) {
    let stream = null, raf = 0;
    const stop = () => { cancelAnimationFrame(raf); stream?.getTracks().forEach((t) => t.stop()); stream = null; };
    const d = dialog((close) => {
      const status = h("p", { class: "help", role: "status" }, "Point the camera at an Unbeatable Bank QR code.");
      const video = h("video", { playsinline: true, muted: true, hidden: true });
      const accept = (text) => {
        const code = parsePayCode(text);
        if (!code) { status.textContent = "That QR code isn't an Unbeatable Bank payment code."; status.className = "help bad"; return false; }
        stop(); close(); onCode(code); return true;
      };
      const file = h("input", { type: "file", accept: "image/*", id: "qr-file", "aria-label": "QR code image" });
      file.addEventListener("change", async () => {
        if (!file.files[0]) return;
        try {
          const text = await decodeQrFile(file.files[0]);
          if (!text) { status.textContent = "We couldn't find a QR code in that picture. Try a sharper one."; status.className = "help bad"; } else accept(text);
        } catch { status.textContent = "That file isn't a picture we can read."; status.className = "help bad"; }
      });
      const startCamera = async (btn) => {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        } catch { status.textContent = "The camera isn't available. Allow camera access, or upload a picture of the code."; status.className = "help bad"; return; }
        btn.hidden = true; video.hidden = false; video.srcObject = stream; await video.play();
        const canvas = h("canvas"); const ctx = canvas.getContext("2d", { willReadFrequently: true });
        const tick = () => {
          if (!stream) return;
          if (video.videoWidth) {
            canvas.width = video.videoWidth; canvas.height = video.videoHeight;
            ctx.drawImage(video, 0, 0);
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const hit = window.jsQR(img.data, img.width, img.height);
            if (hit?.data && accept(hit.data)) return;
          }
          raf = requestAnimationFrame(tick);
        };
        tick();
      };
      const cameraOk = !!navigator.mediaDevices?.getUserMedia && window.isSecureContext;
      return h("div", { class: "body scanner" }, h("h2", null, "Scan to pay"), status, video,
        h("div", { class: "actions", style: "margin:14px 0" },
          cameraOk ? h("button", { type: "button", onclick: (e) => startCamera(e.currentTarget) }, "Use the camera")
            : h("p", { class: "help" }, "The camera only works over a secure (https) connection. Upload a picture of the code instead.")),
        field("Or upload a picture of the code", file),
        h("div", { class: "actions" }, h("button", { class: "quiet", type: "button", onclick: close }, "Cancel")));
    });
    d.addEventListener("gone", stop);
  }

  async function viewPay(query) {
    const [accounts, payments, saved] = await Promise.all([get("/api/me/accounts"), get("/api/payments?limit=30"), get("/api/me/beneficiaries").catch(() => [])]);
    const sources = accounts.filter((a) => payable(a) && a.status === "ACTIVE");
    if (!sources.length) {
      return page("Pay", null, null, empty("You need an active account to pay from",
        accounts.some(payable) ? "Your account is waiting for KYC or is frozen." : "Open a savings or current account first.",
        h("a", { class: "btn", href: "#/accounts" }, "Go to accounts")));
    }
    let channel = query.get("to") ? "QR_PAYMENT" : "TRANSFER";
    let key = uuid();            // one reference per payment attempt: a retry or double-click reuses it
    let checked = null;

    const from = h("select", { name: "from", value: query.get("from") && sources.some((a) => a.account_id === query.get("from")) ? query.get("from") : sources[0].account_id },
      sources.map((a) => h("option", { value: a.account_id }, `${PRODUCT[a.product_type]} ${a.account_number.slice(-4).padStart(8, "•")}, ${inr(a.available_balance ?? 0)} available`)));
    const payee = h("input", { name: "payee", required: true, autocomplete: "off", placeholder: "Account number or pay ID", value: query.get("to") || "", list: "saved-payees" });
    const payeeHelp = h("p", { class: "help", role: "status" }, "We'll show you the name before any money moves.");
    const amount = amountInput("amount", query.get("amount") || "");
    const note = h("input", { name: "note", maxlength: 140, placeholder: "Rent, dinner, thanks…", value: query.get("note") || "" });
    const send = h("button", { type: "submit" }, "Pay");
    const error = h("p", { class: "help bad", role: "alert" });
    const result = h("div", { id: "payment-result", "aria-live": "polite" });

    async function checkPayee() {
      const value = payee.value.trim().replace(/\s/g, "");
      checked = null;
      if (!value) { payeeHelp.className = "help"; payeeHelp.textContent = "We'll show you the name before any money moves."; return null; }
      try {
        const r = value.includes("@") ? await get(`/api/aliases/${encodeURIComponent(value.toLowerCase())}`) : await get(`/api/payees/${encodeURIComponent(value)}`);
        if (payee.value.trim().replace(/\s/g, "") !== value) return null;
        if (!r.can_receive) { payeeHelp.className = "help bad"; payeeHelp.textContent = `${r.display_name} can't receive money right now.`; return null; }
        checked = { value, name: r.display_name };
        payeeHelp.className = "help ok"; payeeHelp.textContent = `Paying ${r.display_name}`;
        return checked;
      } catch (e) {
        payeeHelp.className = "help bad";
        payeeHelp.textContent = e.status === 404 ? "No account matches that. Check the number or pay ID." : e.message;
        return null;
      }
    }
    payee.addEventListener("change", checkPayee);
    payee.addEventListener("input", () => { if (channel === "QR_PAYMENT") channel = "TRANSFER"; key = uuid(); });
    amount.querySelector("input").addEventListener("input", () => { key = uuid(); });
    if (payee.value) checkPayee();

    const form = h("form", { class: "sheet", id: "pay-form" },
      field("From", from),
      field("To", h("div", { class: "inline" }, payee,
        h("button", { class: "quiet", type: "button", id: "scan", onclick: () => scanDialog((code) => {
          payee.value = code.to; channel = "QR_PAYMENT"; key = uuid();
          if (code.amount) amount.querySelector("input").value = code.amount;
          if (code.note) note.value = code.note;
          checkPayee(); amount.querySelector("input").focus();
        }) }, "Scan QR")), payeeHelp),
      h("datalist", { id: "saved-payees" }, saved.map((b) => h("option", { value: b.payee_account_number || b.account_number || "" }, b.nickname || b.display_name || ""))),
      field("Amount", amount),
      field(h("span", null, "Note ", h("span", { class: "hint" }, "(optional)")), note),
      h("div", { class: "field" }, error), send);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (send.disabled) return;
      error.textContent = "";
      const paise = toPaise(amount.querySelector("input").value);
      if (!paise) { error.textContent = "Enter an amount in rupees."; return; }
      send.disabled = true; send.textContent = "Paying…";
      try {
        const target = (checked && checked.value === payee.value.trim().replace(/\s/g, "")) ? checked : await checkPayee();
        if (!target) { error.textContent = "Check who you're paying first."; return; }
        const body = { from_account_id: from.value, payee: target.value.includes("@") ? target.value.toLowerCase() : target.value, amount: paise, channel };
        if (note.value.trim()) body.note = note.value.trim();
        const r = await api("POST", "/api/payments", body, { headers: { "Idempotency-Key": key }, full: true, accept: [422] });
        if (!r.data?.txn_id) throw new ApiError(r.status, messageOf(r.data, r.status));
        result.replaceChildren(slip(r.data, { fresh: true }));
        result.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
        key = uuid();
        if (r.data.status === "COMPLETED") { amount.querySelector("input").value = ""; note.value = ""; }
        refreshHistory(); pollInbox();
      } catch (ex) {
        error.textContent = ex.status === 0 || ex.status >= 500
          ? ex.message + " It's safe to press Pay again: the same payment can't go through twice." : ex.message;
      } finally { send.disabled = false; send.textContent = "Pay"; }
    });

    const history = h("div", { id: "payment-history" });
    const drawHistory = (items) => history.replaceChildren(items.length
      ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, items.map((p) => paymentRow(p, openPayment))))
      : empty("No payments yet", "Your first payment will appear here."));
    const refreshHistory = async () => { try { drawHistory(await get("/api/payments?limit=30")); } catch { /* keep the old list */ } };
    drawHistory(payments);

    return page("Pay", "Pay any Unbeatable Bank account by number, pay ID or QR code.",
      h("a", { class: "btn quiet", href: "#/receive" }, "Receive by QR"),
      h("div", { class: "grid-2" }, form, result),
      section("Payments", null, history));
  }

  function drawQr(canvas, text) {
    const qr = window.qrcode(0, "M");
    qr.addData(text); qr.make();
    const n = qr.getModuleCount(), cell = 8, quiet = 4;
    canvas.width = canvas.height = (n + quiet * 2) * cell;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#121c4d";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * cell, (r + quiet) * cell, cell, cell);
  }

  async function viewReceive(query) {
    const accounts = (await get("/api/me/accounts")).filter((a) => payable(a) && a.status === "ACTIVE");
    if (!accounts.length) return page("Receive", null, null, empty("You need an active account first", "Open an account and get your KYC verified.", h("a", { class: "btn", href: "#/accounts" }, "Go to accounts")));
    const account = h("select", { value: accounts.some((a) => a.account_id === query.get("account")) ? query.get("account") : accounts[0].account_id },
      accounts.map((a) => h("option", { value: a.account_id }, `${PRODUCT[a.product_type]} ${groupDigits(a.account_number)}`)));
    const amount = amountInput("amount", "", { required: false });
    const note = h("input", { maxlength: 60, placeholder: "What it's for" });
    const canvas = h("canvas", { id: "qr", role: "img", "aria-label": "Payment QR code" });
    const link = h("input", { readonly: true, id: "pay-link", "aria-label": "Payment link" });
    const caption = h("p", { class: "muted", style: "margin-top:10px" });
    const draw = () => {
      const a = accounts.find((x) => x.account_id === account.value);
      const q = new URLSearchParams({ to: a.account_number });
      const paise = toPaise(amount.querySelector("input").value);
      if (paise) q.set("amount", (paise / 100).toFixed(2));
      if (note.value.trim()) q.set("note", note.value.trim());
      link.value = `${location.origin}${location.pathname}#/pay?${q}`;
      drawQr(canvas, link.value);
      caption.textContent = paise ? `Asks for ${inr(paise)} into account ${groupDigits(a.account_number)}` : `Pays into account ${groupDigits(a.account_number)}; the payer chooses the amount`;
    };
    [account, amount.querySelector("input"), note].forEach((el) => el.addEventListener("input", draw));
    draw();
    return page("Receive by QR", "Show this code to someone with the Unbeatable Bank app, or send them the link.", null,
      h("div", { class: "grid-2" },
        h("div", { class: "sheet" }, field("Into", account),
          field(h("span", null, "Amount ", h("span", { class: "hint" }, "(optional)")), amount),
          field(h("span", null, "Note ", h("span", { class: "hint" }, "(optional)")), note)),
        h("div", { class: "sheet qr-card" }, canvas, caption,
          h("div", { class: "inline", style: "margin-top:14px" }, link, h("button", { class: "quiet", type: "button", onclick: () => copy(link.value, "Payment link") }, "Copy link")),
          h("div", { class: "actions", style: "justify-content:center;margin-top:12px" },
            h("button", { class: "quiet small", type: "button", onclick: () => { const a = h("a", { href: canvas.toDataURL("image/png"), download: "unbeatable-bank-qr.png" }); a.click(); } }, "Download the code")))));
  }

  // ------------------------------------------------------------------ customer: deposits

  const span = (r) => {
    const f = (d) => (d >= 365 ? `${+(d / 365).toFixed(1)} yr` : `${d} days`);
    return `${f(r.min_days)} to ${f(r.max_days)}`;
  };

  async function viewDeposits() {
    const [rates, deposits, accounts] = await Promise.all([get("/api/deposits/rates"), get("/api/deposits"), get("/api/me/accounts")]);
    const sources = accounts.filter((a) => payable(a) && a.status === "ACTIVE");
    let product = "FD", key = uuid(), timer = 0, seq = 0;

    const amount = amountInput("amount", "50000");
    const months = h("input", { name: "tenure", type: "number", min: 1, max: 120, value: 12, required: true });
    const funding = h("select", { name: "funding" }, sources.map((a) => h("option", { value: a.account_id }, `${PRODUCT[a.product_type]} ${a.account_number.slice(-4).padStart(8, "•")}, ${inr(a.available_balance ?? 0)}`)));
    const amountLabel = h("span", null, "Amount to deposit");
    const quoteBox = h("div", { id: "quote", "aria-live": "polite" });
    const error = h("p", { class: "help bad", role: "alert" });
    const book = h("button", { type: "submit" }, "Book fixed deposit");

    async function quote() {
      const paise = toPaise(amount.querySelector("input").value), m = parseInt(months.value, 10);
      const my = ++seq;
      if (!paise || !m) { quoteBox.replaceChildren(h("p", { class: "muted" }, "Enter an amount and a term to see what you'd earn.")); return; }
      try {
        const q = await get(`/api/deposits/quote?product=${product}&amount=${paise}&tenure_months=${m}`);
        if (my !== seq) return;
        quoteBox.replaceChildren(h("dl", { class: "facts" },
          h("dt", null, "Interest rate"), h("dd", { class: "num" }, pct(q.annual_rate), " a year, compounded quarterly"),
          h("dt", null, product === "RD" ? "You put in" : "You deposit"), h("dd", { class: "num" }, inr(q.total_deposited)),
          h("dt", null, "Interest earned"), h("dd", { class: "num", style: "color:var(--green)" }, "+" + inr(q.interest)),
          h("dt", null, "You get back"), h("dd", { class: "num", style: "font:700 1.5rem var(--display)" }, inr(q.maturity_amount)),
          h("dt", null, "On"), h("dd", null, day(q.maturity_date))));
      } catch (e) { if (my === seq) quoteBox.replaceChildren(h("p", { class: "help bad" }, e.message)); }
    }
    const requote = () => { key = uuid(); clearTimeout(timer); timer = setTimeout(quote, 250); };
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Deposit type" }, ["FD", "RD"].map((p) =>
      h("button", { type: "button", "aria-pressed": String(p === product), "data-product": p, onclick: (e) => {
        product = p;
        seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
        amountLabel.textContent = p === "FD" ? "Amount to deposit" : "Monthly instalment";
        book.textContent = p === "FD" ? "Book fixed deposit" : "Start recurring deposit";
        requote();
      } }, p === "FD" ? "Fixed deposit" : "Recurring deposit")));
    [amount.querySelector("input"), months].forEach((el) => el.addEventListener("input", requote));

    const form = h("form", { class: "sheet", id: "deposit-form" }, h("div", { class: "field" }, seg),
      h("div", { class: "row" }, field(amountLabel, amount), field("Term in months", months)),
      sources.length ? field("Pay from", funding) : h("div", { class: "notice" }, "You need an active savings or current account to fund a deposit."),
      h("div", { class: "field" }, error), sources.length ? book : null);
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); error.textContent = "";
      const paise = toPaise(amount.querySelector("input").value);
      if (!paise) { error.textContent = "Enter an amount in rupees."; return; }
      book.disabled = true;
      try {
        const r = await api("POST", `/api/deposits/${product.toLowerCase()}`, { funding_account_id: funding.value, amount: paise, tenure_months: parseInt(months.value, 10) },
          { headers: { "Idempotency-Key": key }, full: true, accept: [422] });
        if (r.data?.status === "FAILED") throw new Error(`We couldn't book this deposit: ${words(r.data.failure_code || "payment failed")}.`);
        if (r.status === 422) throw new ApiError(422, messageOf(r.data, 422));
        toast(`${PRODUCT[product]} booked: ${inr(paise)} at ${pct(r.data.annual_rate)}`);
        render();
      } catch (ex) { error.textContent = ex.message; } finally { book.disabled = false; }
    });
    quote();

    function closeEarly(d) {
      busyQuote(d);
    }
    async function busyQuote(d) {
      let q;
      try { q = await get(`/api/deposits/${d.deposit_id}/closure-quote`); } catch (e) { toast(e.message, true); return; }
      formDialog({
        title: "Close this deposit early?", submit: `Close and take ${inr(q.payout_amount)}`, danger: true,
        lead: `You've held it for ${q.held_days} days. Closing early pays a lower rate than the one you booked.`,
        fields: h("dl", { class: "facts", style: "margin-bottom:18px" },
          h("dt", null, "Booked rate"), h("dd", { class: "num" }, pct(q.booked_rate)),
          h("dt", null, "Early-closure rate"), h("dd", { class: "num" }, pct(q.closure_rate)),
          h("dt", null, "You put in"), h("dd", { class: "num" }, inr(q.deposited)),
          h("dt", null, "You get now"), h("dd", { class: "num" }, h("b", null, inr(q.payout_amount))),
          h("dt", null, "If you keep it"), h("dd", { class: "num" }, inr(q.maturity_amount_if_kept))),
        onSubmit: async () => { await api("POST", `/api/deposits/${d.deposit_id}/close`, undefined, { headers: { "Idempotency-Key": uuid() } }); toast("Deposit closed. The money is in your account."); render(); },
      });
    }

    const mine = deposits.map((d) => {
      const total = new Date(d.maturity_date) - new Date(d.start_date);
      const done = Math.max(0, Math.min(1, (new Date(rates.business_date) - new Date(d.start_date)) / (total || 1)));
      const live = d.status === "ACTIVE";
      return h("li", { "data-deposit": d.deposit_id },
        h("div", { class: "main" },
          h("strong", null, `${PRODUCT[d.product]} of ${inr(d.principal)}${d.product === "RD" ? " a month" : ""}`),
          h("span", { class: "muted small" }, `${pct(d.annual_rate)} for ${d.tenure_months} months, matures ${day(d.maturity_date)}`,
            d.product === "RD" ? `. ${d.installments_paid} paid${d.installments_missed ? `, ${d.installments_missed} missed` : ""}` : ""),
          live && h("div", { class: "meter good", role: "img", "aria-label": `${Math.round(done * 100)}% of the term done` }, h("span", { style: `width:${Math.round(done * 100)}%` }))),
        h("div", { class: "actions" },
          h("div", { class: "right" }, h("b", { class: "num" }, inr(live ? d.balance : (d.payout_amount ?? d.balance))),
            h("div", { class: "muted small" }, live ? `grows to ${inr(d.projected_maturity)}` : (d.payout_amount != null ? "paid out" : ""))),
          statusTag(d.status),
          live && h("button", { class: "quiet small", type: "button", onclick: () => closeEarly(d) }, "Close early")));
    });

    const rateRows = (p) => rates.rates.filter((r) => r.product === p).map((r) => h("tr", null, h("td", null, span(r)), h("td", { class: "right num" }, pct(r.annual_rate))));
    return page("Deposits", "Lock money away for a fixed term and earn more than a savings account. Open as many as you like.", null,
      h("div", { class: "grid-2" }, form, h("div", { class: "sheet" }, h("h3", { style: "margin-bottom:14px" }, "What you'd earn"), quoteBox)),
      section("Your deposits", null, mine.length ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, mine))
        : empty("No deposits yet", "Book one above. Interest is added every quarter.")),
      section("Today's rates", h("span", { class: "muted small" }, `Bank date ${day(rates.business_date)}`),
        h("div", { class: "grid-2" }, ["FD", "RD"].map((p) => h("div", { class: "sheet flush table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, `${PRODUCT[p]} term`), h("th", { class: "right" }, "Rate a year"))), h("tbody", null, rateRows(p))))))));
  }

  // ------------------------------------------------------------------ customer: inbox and security

  async function viewInbox() {
    const items = (await pollInbox()) || await get("/api/notifications?limit=50");
    const TONE = { OTP: "warn", SECURITY: "bad", FRAUD: "bad", TXN: "" };
    const rows = items.map((n) => {
      const li = h("li", { class: n.read ? "" : "unread", "data-type": n.type },
        h("div", { class: "main" }, h("strong", null, n.title), h("span", null, n.body), h("div", { class: "muted small" }, when(n.created_at))),
        h("div", { class: "actions" }, tag(n.type === "OTP" ? "Code" : words(n.type), TONE[n.type] || ""),
          !n.read && h("button", { class: "quiet small", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
            await api("POST", `/api/notifications/${n.id}/read`); li.classList.remove("unread"); e.currentTarget?.remove(); pollInbox();
          }) }, "Mark read")));
      return li;
    });
    return page("Inbox", "Confirmation codes, payment receipts and security alerts. In production these also go out by email.", null,
      rows.length ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, rows))
        : empty("Nothing here yet", "You'll hear from us when money moves or something needs your attention."));
  }

  async function viewSecurity() {
    const [me, devices, sessions, logins, kyc] = await Promise.all([loadMe(), get("/api/auth/devices"), get("/api/auth/sessions"),
      get("/api/auth/login-history"), get("/api/kyc/documents")]);
    const flags = (l) => [l.new_device && tag("New device", "warn"), l.impossible_travel && tag("Impossible travel", "bad")];
    return page("Security", "Who you are to the bank, and where your account has been used.",
      h("button", { class: "quiet", type: "button", onclick: () => signOut() }, "Sign out"),
      kycNotice(me),
      h("div", { class: "sheet" }, h("dl", { class: "facts" },
        h("dt", null, "Name"), h("dd", null, me.full_name),
        h("dt", null, "Customer number"), h("dd", { class: "num" }, me.cif_number),
        h("dt", null, "Customer ID"), h("dd", { class: "small" }, me.customer_id, " ", copyBtn(me.customer_id, "Customer ID")),
        h("dt", null, "KYC"), h("dd", null, statusTag(me.kyc_status)),
        h("dt", null, "Email"), h("dd", null, me.email),
        h("dt", null, "Mobile"), h("dd", { class: "num" }, me.phone),
        me.pan_last4 && [h("dt", null, "PAN"), h("dd", { class: "num" }, "•••••" + me.pan_last4)],
        h("dt", null, "Daily transfer limit"), h("dd", { class: "num" }, inr(me.daily_transfer_limit)))),
      section("Identity documents", h("span", { class: "muted small" }, `JPEG, PNG or PDF, up to ${kyc.max_bytes / 1048576} MB each`),
        kycPanel(kyc, render),
        h("p", { class: "muted small", style: "margin-top:10px" }, "Only KYC officers can open these, and every time one does it is recorded. A document we don't accept is deleted straight away.")),
      section("Devices that have signed in", null, h("div", { class: "sheet flush" }, h("ul", { class: "list" }, devices.map((d) =>
        h("li", null, h("div", { class: "main" }, h("strong", null, d.label || "Unnamed device"),
          h("span", { class: "muted small" }, `First seen ${when(d.first_seen)}, last used ${when(d.last_seen)}${d.last_ip ? ` from ${d.last_ip}` : ""}`)),
          h("button", { class: "quiet small danger", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
            await api("DELETE", `/api/auth/devices/${d.device_id}`); toast("Device removed and signed out"); render();
          }) }, "Remove")))))),
      section("Active sessions", null, h("div", { class: "sheet flush" }, h("ul", { class: "list" }, sessions.map((x) =>
        h("li", null, h("div", { class: "main" }, h("strong", null, x.ip || "Unknown address"), h("span", { class: "muted small" }, `Since ${when(x.created_at)}. ${x.user_agent || ""}`))))))),
      section("Sign-in history", null, h("div", { class: "sheet flush table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, h("th", null, "When"), h("th", null, "Result"), h("th", null, "From"), h("th", null, "Notes"))),
        h("tbody", null, logins.map((l) => h("tr", null, h("td", { class: "nowrap" }, when(l.at)),
          h("td", null, tag(words(l.result), /SUCCESS|OK/i.test(l.result) ? "good" : "bad")), h("td", { class: "num" }, l.ip || ""), h("td", null, h("div", { class: "actions" }, flags(l))))))))));
  }

  // ------------------------------------------------------------------ staff: fraud desk

  async function viewFraudDesk() {
    const [cases, alerts, held, stats] = await Promise.all([get("/api/staff/cases"), get("/api/staff/alerts?limit=30"), get("/api/staff/payments?status=HELD"), get("/api/staff/analytics")]);
    const open = cases.filter((c) => c.status !== "RESOLVED");
    const count = (f) => stats.cases.filter(f).reduce((n, c) => n + c.n, 0);
    const caseRows = (list) => list.map((c) => h("tr", { class: "clickable", tabindex: 0, onclick: () => go("/staff/cases/" + c.case_id),
      onkeydown: (e) => { if (e.key === "Enter") go("/staff/cases/" + c.case_id); } },
      h("td", { class: "nowrap" }, h("a", { href: "#/staff/cases/" + c.case_id }, c.case_number)), h("td", null, statusTag(c.severity)),
      h("td", null, words(c.type)), h("td", null, c.summary), h("td", { class: "nowrap" }, when(c.created_at)), h("td", null, statusTag(c.status))));
    const caseTable = (list) => h("div", { class: "sheet flush table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "Case"), h("th", null, "Severity"), h("th", null, "Type"), h("th", null, "Summary"), h("th", null, "Opened"), h("th", null, "Status"))),
      h("tbody", null, caseRows(list))));
    return page("Fraud desk", "Payments the risk engine stopped, patterns the stream job found, and reports from customers.", null,
      h("div", { class: "stats" },
        h("div", { class: "stat" }, h("b", null, count((c) => c.status !== "RESOLVED")), h("span", null, "Open cases")),
        h("div", { class: "stat" }, h("b", null, count((c) => c.status !== "RESOLVED" && ["HIGH", "CRITICAL"].includes(c.severity))), h("span", null, "High severity")),
        h("div", { class: "stat" }, h("b", null, held.length), h("span", null, "Payments on hold")),
        h("div", { class: "stat" }, h("b", null, stats.pending_approvals), h("span", null, "Approvals waiting"))),
      section("Open cases", null, open.length ? caseTable(open) : empty("No open cases", "New cases appear when a payment is held or a pattern is detected.")),
      section("Payments on hold", null, held.length ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, held.map((p) => h("li", null,
        h("div", { class: "main" }, h("strong", null, `${inr(p.amount)} to ${p.to_display_name || p.payee_ref}`),
          h("span", { class: "muted small" }, `${when(p.created_at)}, score ${p.risk_score ?? "n/a"}: ${(p.risk_reasons || []).map(words).join(", ")}`)),
        h("button", { class: "quiet small", type: "button", onclick: () => openPayment(p, true) }, "Details")))))
        : empty("Nothing on hold", "Held payments are released or cancelled by resolving their case.")),
      section("Latest alerts", null, alerts.length ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, alerts.map((a) => h("li", null,
        h("div", { class: "main" }, h("strong", null, a.title), h("span", { class: "muted small" }, `${words(a.type)}, ${when(a.created_at)}`,
          a.detail?.reasons ? `: ${a.detail.reasons.map(words).join(", ")}` : "")),
        h("div", { class: "actions" }, statusTag(a.severity), a.case_id && h("a", { class: "btn quiet small", href: "#/staff/cases/" + a.case_id }, "Open case"))))))
        : empty("No alerts", "Alerts arrive from the payment pipeline, logins and the Flink job.")),
      cases.length > open.length && section("Resolved cases", null, caseTable(cases.filter((c) => c.status === "RESOLVED").slice(0, 20))));
  }

  async function viewCase(caseId) {
    const c = await get(`/api/staff/cases/${caseId}`);
    const note = h("textarea", { name: "note", required: true, minlength: 3, placeholder: "What you checked and why you decided this" });
    const error = h("p", { class: "help bad", role: "alert" });
    const decide = (resolution, label, cls) => h("button", { type: "button", class: cls, onclick: async (e) => {
      error.textContent = "";
      if (note.value.trim().length < 3) { error.textContent = "Write a short note first."; note.focus(); return; }
      const btn = e.currentTarget; btn.disabled = true;
      try { await api("POST", `/api/staff/cases/${caseId}/resolve`, { resolution, note: note.value.trim() }); toast(`${c.case_number} resolved`); render(); }
      catch (ex) { error.textContent = ex.message; } finally { btn.disabled = false; }
    } }, label);
    return page(c.case_number, c.summary, h("a", { class: "btn quiet", href: "#/staff/fraud" }, "Back to the fraud desk"),
      h("div", { class: "grid-2" },
        h("div", null,
          h("div", { class: "sheet" }, h("dl", { class: "facts" },
            h("dt", null, "Status"), h("dd", null, statusTag(c.status), c.resolution ? " " + words(c.resolution) : ""),
            h("dt", null, "Severity"), h("dd", null, statusTag(c.severity)),
            h("dt", null, "Type"), h("dd", null, words(c.type)),
            h("dt", null, "Opened"), h("dd", null, when(c.created_at)),
            h("dt", null, "Customer"), h("dd", { class: "small" }, c.customer_id || "n/a", c.customer_id && [" ", h("a", { href: `#/staff/branch?q=${c.customer_id}` }, "View")]),
            h("dt", null, "Account"), h("dd", { class: "small" }, c.account_id || "n/a", c.account_id && [" ", copyBtn(c.account_id, "Account ID")]),
            c.resolved_by && [h("dt", null, "Resolved by"), h("dd", null, c.resolved_by)],
            c.resolution_note && [h("dt", null, "Note"), h("dd", null, c.resolution_note)]),
            c.reasons?.length ? h("div", { class: "reasons", style: "margin-top:14px" }, c.reasons.map((r) => tag(words(r), "warn"))) : null),
          c.status !== "RESOLVED" && h("div", { class: "sheet", style: "margin-top:20px" }, h("h3", { style: "margin-bottom:12px" }, "Decide"),
            field("Note for the record", note), error,
            h("div", { class: "actions" },
              decide("FALSE_POSITIVE", "Genuine: release the payment", ""),
              decide("CONFIRMED_FRAUD", "Fraud: cancel, freeze, deny-list payee", "danger")))),
        h("div", null, c.payment ? slip(c.payment, { staff: true }) : h("div", { class: "sheet muted" }, "This case isn't tied to a single payment."))),
      section("Timeline", null, h("div", { class: "sheet flush" }, h("ul", { class: "list" }, (c.timeline || []).map((t) => h("li", null,
        h("div", { class: "main" }, h("strong", null, words(t.event_type)),
          h("span", { class: "muted small" }, `${when(t.created_at)} by ${t.actor}`),
          t.detail && (t.detail.note || t.detail.summary) && h("div", null, t.detail.note || t.detail.summary))))))));
  }

  async function viewApprovals() {
    const list = await get("/api/staff/approvals");
    const me = store.session.name;
    const act = (a, verb) => h("button", { class: verb === "approve" ? "small" : "small quiet danger", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
      await api("POST", `/api/staff/approvals/${a.approval_id}/${verb}`); toast(verb === "approve" ? "Approved and carried out" : "Rejected"); render();
    }) }, verb === "approve" ? "Approve" : "Reject");
    const row = (a) => h("li", { "data-approval": a.approval_id },
      h("div", { class: "main" }, h("strong", null, `${words(a.action)} ${short(a.target_id)}…`), h("span", null, a.reason),
        h("div", { class: "muted small" }, `Requested by ${a.requested_by}${a.created_at ? ", " + when(a.created_at) : ""}${a.decided_by ? `. Decided by ${a.decided_by}` : ""}`)),
      h("div", { class: "actions" }, statusTag(a.status), a.status === "PENDING" && [act(a, "approve"), act(a, "reject")]));
    const pending = list.filter((a) => a.status === "PENDING"), decided = list.filter((a) => a.status !== "PENDING");
    return page("Approvals", "Sensitive actions need two people: one asks, a different one approves.",
      h("button", { type: "button", onclick: () => formDialog({
        title: "Ask to unfreeze an account", submit: "Send for approval",
        lead: "Another analyst or an administrator has to approve it. You can't approve your own request.",
        fields: [field("Account ID", h("input", { name: "target_id", required: true, autocomplete: "off" }), "Copy it from the case or the branch desk."),
          field("Reason", h("textarea", { name: "reason", required: true, minlength: 5 }))],
        onSubmit: async (v) => { await api("POST", "/api/staff/approvals", { action: "UNFREEZE_ACCOUNT", target_id: v.target_id.trim(), reason: v.reason.trim() }); toast("Sent for approval"); render(); },
      }) }, "Ask to unfreeze an account"),
      section("Waiting", h("span", { class: "muted small" }, `Signed in as ${me}`), pending.length ? h("div", { class: "sheet flush" }, h("ul", { class: "list" }, pending.map(row)))
        : empty("Nothing is waiting", "Requests to unfreeze accounts appear here for a second officer.")),
      decided.length ? section("Decided", null, h("div", { class: "sheet flush" }, h("ul", { class: "list" }, decided.slice(0, 30).map(row)))) : null);
  }

  // ------------------------------------------------------------------ staff: branch desk

  async function viewBranch(query) {
    const results = h("div", { id: "customer-results" });
    const q = h("input", { name: "q", type: "search", placeholder: "Customer number, email, mobile, name or account number", value: query.get("q") || "", "aria-label": "Find a customer" });

    function cashDialog(account) {
      formDialog({
        title: "Cash deposit", submit: "Deposit cash",
        lead: `Into account ${groupDigits(account.account_number)}. Count the cash before you confirm.`,
        fields: field("Amount", amountInput("amount", "")),
        onSubmit: (() => { const key = uuid(); return async (v) => {
          const paise = toPaise(v.amount);
          if (!paise) throw new Error("Enter an amount in rupees.");
          await api("POST", "/api/staff/cash-deposits", { account_number: account.account_number, amount: paise }, { headers: { "Idempotency-Key": key } });
          toast(`${inr(paise)} deposited`); search();
        }; })(),
      });
    }

    const DOC_LABEL = { PAN: "PAN card", ADDRESS_PROOF: "Address proof", PHOTO: "Photograph" };

    // KYC officer: the customer's documents with View, Approve and Reject.
    function documentsBlock(c, docs) {
      if (!docs.length) return h("p", { class: "muted small", style: "margin:4px 0 14px" }, "No identity documents uploaded yet.");
      return h("ul", { class: "list", style: "margin:0 -20px 14px;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)" }, docs.map((d) =>
        h("li", { "data-document": d.document_id, "data-doc-type": d.doc_type },
          h("div", { class: "main" }, h("strong", null, DOC_LABEL[d.doc_type]),
            h("span", { class: "muted small" }, `${d.content_type === "application/pdf" ? "PDF" : "Picture"}, ${(d.size_bytes / 1024).toFixed(0)} KB, sent ${when(d.uploaded_at)}`,
              d.decided_by ? `. ${words(d.status)} by ${d.decided_by.split(":")[1]}` : "", d.reject_reason ? `: ${d.reject_reason}` : ""),
            d.same_file_other_customers > 0 && h("div", null, tag(`Same file also uploaded by ${d.same_file_other_customers} other customer${d.same_file_other_customers > 1 ? "s" : ""}`, "bad"))),
          h("div", { class: "actions" }, statusTag(d.status),
            d.has_file && h("button", { class: "quiet small", type: "button", onclick: (e) => busy(e.currentTarget, () =>
              viewFile(`/api/staff/kyc-documents/${d.document_id}/content`, `${DOC_LABEL[d.doc_type]} of ${c.full_name}`)) }, "View"),
            d.status === "PENDING" && h("button", { class: "small", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
              const r = await api("POST", `/api/staff/kyc-documents/${d.document_id}/approve`);
              toast(r.kyc_verified ? `All documents accepted: ${c.full_name} is verified` : `${DOC_LABEL[d.doc_type]} accepted`); search();
            }) }, "Approve"),
            d.status === "PENDING" && h("button", { class: "quiet small danger", type: "button", onclick: () => formDialog({
              title: `Reject this ${DOC_LABEL[d.doc_type].toLowerCase()}`, submit: "Reject and delete the file", danger: true,
              lead: "The customer sees your reason and is asked to upload again. The file is deleted.",
              fields: field("Reason", h("input", { name: "reason", required: true, minlength: 5, maxlength: 300, placeholder: "The picture is too blurred to read" })),
              onSubmit: async (v) => { await api("POST", `/api/staff/kyc-documents/${d.document_id}/reject`, { reason: v.reason.trim() }); toast("Rejected; the customer has been told"); search(); },
            }) }, "Reject")))));
    }

    async function customerCard(c) {
      let accounts = [], docs = [];
      try { accounts = await get(`/api/staff/customers/${c.customer_id}/accounts`); } catch { /* shown as none */ }
      if (can("KYC")) { try { docs = await get(`/api/staff/customers/${c.customer_id}/kyc-documents`); } catch { /* shown as none */ } }
      return h("div", { class: "sheet", "data-customer": c.customer_id, style: "margin-bottom:16px" },
        h("div", { class: "section-head" }, h("div", null, h("h3", null, c.full_name), h("span", { class: "muted small num" }, `${c.cif_number}, ${c.email}, ${c.phone}`)),
          h("div", { class: "actions" }, h("span", null, "KYC ", statusTag(c.kyc_status)),
            c.kyc_status !== "VERIFIED" && can("KYC") && h("button", { class: "small", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
              await api("POST", `/api/staff/kyc/${c.customer_id}/verify`); toast(`${c.full_name} is verified`); search();
            }) }, "Verify in person"))),
        can("KYC") && documentsBlock(c, docs),
        accounts.length ? h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, "Account"), h("th", null, "Type"), h("th", null, "Status"), h("th", null, ""))),
          h("tbody", null, accounts.map((a) => h("tr", null, h("td", { class: "num nowrap" }, groupDigits(a.account_number)), h("td", null, PRODUCT[a.product_type] || a.product_type),
            h("td", null, statusTag(a.status)),
            h("td", null, h("div", { class: "actions", style: "justify-content:flex-end" },
              payable(a) && can("TELLER") && h("button", { class: "quiet small", type: "button", onclick: () => cashDialog(a) }, "Cash deposit"),
              h("button", { class: "quiet small", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
                const r = await get(`/api/staff/accounts/${a.account_id}/audit`);
                toast(r.chain_valid ? "Audit trail intact: every event links to the one before it" : "Audit trail broken: events were altered", !r.chain_valid);
              }) }, "Check audit trail"),
              copyBtn(a.account_id, "Account ID")))))))) : h("p", { class: "muted" }, "No accounts yet."));
    }

    async function search() {
      const term = q.value.trim();
      results.replaceChildren(h("p", { class: "skeleton" }, "Searching…"));
      try {
        let found = await get(term ? `/api/staff/customers?query=${encodeURIComponent(term)}` : "/api/staff/customers?kyc_status=PENDING&limit=20");
        let waiting = [];
        if (!term && can("KYC")) {
          // Customers with documents to review come first, oldest upload first.
          waiting = await get("/api/staff/kyc-queue");
          const order = [...new Set(waiting.map((d) => d.customer_id))];
          const known = new Map(found.map((c) => [c.customer_id, c]));
          const extra = await Promise.all(order.filter((id) => !known.has(id)).map((id) => get(`/api/staff/customers/${id}`).catch(() => null)));
          extra.filter(Boolean).forEach((c) => known.set(c.customer_id, c));
          found = [...order.filter((id) => known.has(id)).map((id) => known.get(id)), ...found.filter((c) => !order.includes(c.customer_id))];
        }
        const cards = await Promise.all(found.map(customerCard));
        results.replaceChildren(h("h2", { style: "margin-bottom:12px", id: "branch-heading" }, term ? `${found.length} found`
          : waiting.length ? `${waiting.length} document${waiting.length > 1 ? "s" : ""} to review` : "Waiting for KYC"),
          cards.length ? h("div", null, cards) : empty(term ? "No customer matches that" : "No one is waiting", term ? "Try the customer number, full email or mobile with +91." : "New sign-ups appear here until their KYC is verified."));
      } catch (e) { results.replaceChildren(h("div", { class: "notice bad" }, e.message)); }
    }
    const form = h("form", { class: "inline", role: "search", style: "margin-bottom:24px" }, q, h("button", { type: "submit" }, "Find"));
    form.addEventListener("submit", (e) => { e.preventDefault(); search(); });
    search();
    return page("Branch desk", "Verify new customers, take cash deposits and look up accounts.", null, form, results);
  }

  // ------------------------------------------------------------------ staff: system

  function genericTable(rows) {
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((k) => typeof rows[0][k] !== "object" || rows[0][k] === null).slice(0, 9);
    return h("div", { class: "sheet flush table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, cols.map((c) => h("th", null, words(c))))),
      h("tbody", null, rows.map((r) => h("tr", null, cols.map((c) => h("td", { class: typeof r[c] === "number" ? "num" : "" }, r[c] == null ? "" : String(r[c]))))))));
  }

  async function viewSystem() {
    const safe = (p) => get(p).catch((e) => ({ __error: e.message }));
    const [dyna, recon, clock, stats, limits, rules] = await Promise.all([
      safe("/api/staff/ops/dynastore"), safe("/api/staff/ops/reconciliation"), safe("/api/staff/ops/clock"),
      safe("/api/staff/analytics"), safe("/api/staff/ops/rate-limits"), safe("/api/staff/ops/risk-rules")]);
    const failed = (x) => x.__error && h("div", { class: "notice bad" }, x.__error);

    const keyOut = h("div", { style: "margin-top:12px" });
    const keyForm = h("form", { class: "inline" }, h("input", { name: "key", placeholder: "A key, for example rl:payments:customer:<id>", "aria-label": "Key" }), h("button", { class: "quiet", type: "submit" }, "Read key"));
    keyForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = new FormData(keyForm).get("key").trim();
      if (!key) return;
      try { keyOut.replaceChildren(h("pre", { class: "json" }, JSON.stringify(await get(`/api/staff/ops/dynastore/keys/${encodeURI(key)}`), null, 2))); }
      catch (ex) { keyOut.replaceChildren(h("p", { class: "help bad" }, ex.status === 404 ? "No value under that key." : ex.message)); }
    });

    const setFault = (body, done) => (e) => busy(e.currentTarget, async () => { await api("POST", "/api/staff/ops/dynastore/faults", body); toast(done); render(); });
    const dynaPanel = dyna.__error ? failed(dyna) : [
      h("div", { class: "stats", style: "margin-bottom:12px" },
        h("div", { class: "stat" }, h("b", null, `N=${dyna.n} R=${dyna.r} W=${dyna.w}`), h("span", null, "Replicas, read and write quorum")),
        h("div", { class: "stat" }, h("b", null, dyna.keys), h("span", null, `Keys on ${dyna.node_id}`)),
        h("div", { class: "stat" }, h("b", null, dyna.pending_hints), h("span", null, "Hints waiting for handoff"))),
      h("div", { class: "ring" }, dyna.nodes.map((n) => {
        const down = !n.alive || (n.is_self && dyna.simulated_down) || dyna.blocked_peers.includes(n.node_id);
        const share = dyna.ownership?.[n.node_id];
        return h("div", { class: "node" + (down ? " down" : ""), "data-node": n.node_id }, h("strong", null, n.node_id),
          h("span", { class: "muted small" }, n.address), h("div", { style: "margin-top:8px" }, tag(down ? (n.alive ? "Cut off" : "Down") : "Alive", down ? "bad" : "good"),
            n.is_self ? " " : null, n.is_self ? tag("Answering") : null),
          share != null && h("div", { class: "meter", title: "Share of the key ring" }, h("span", { style: `width:${Math.round(share * 100)}%` })),
          share != null && h("span", { class: "muted small" }, `${(share * 100).toFixed(1)}% of the ring`),
          can() && !n.is_self && h("div", { style: "margin-top:10px" }, dyna.blocked_peers.includes(n.node_id)
            ? h("button", { class: "small quiet", type: "button", onclick: setFault({ blocked_peers: dyna.blocked_peers.filter((x) => x !== n.node_id), down: dyna.simulated_down }, `${n.node_id} reconnected`) }, "Reconnect")
            : h("button", { class: "small quiet danger", type: "button", onclick: setFault({ blocked_peers: [...dyna.blocked_peers, n.node_id], down: dyna.simulated_down }, `${dyna.node_id} can no longer reach ${n.node_id}`) }, "Cut the link")));
      })),
      can() && h("div", { class: "actions", style: "margin-top:14px" },
        dyna.simulated_down
          ? h("button", { type: "button", onclick: setFault({ blocked_peers: dyna.blocked_peers, down: false }, `${dyna.node_id} is back`) }, `Bring ${dyna.node_id} back`)
          : h("button", { class: "danger quiet", type: "button", onclick: setFault({ blocked_peers: dyna.blocked_peers, down: true }, `${dyna.node_id} is refusing requests`) }, `Take ${dyna.node_id} down`),
        (dyna.blocked_peers.length > 0 || dyna.simulated_down) && h("button", { class: "quiet", type: "button", onclick: setFault({ blocked_peers: [], down: false }, "All faults cleared") }, "Clear all faults"),
        h("span", { class: "muted small" }, "With one node out, payments still work: writes go to a stand-in and are handed back later.")),
      h("div", { style: "margin-top:16px" }, keyForm, keyOut)];

    const days = h("input", { name: "days", type: "number", min: 1, max: 400, value: 30, style: "max-width:110px", "aria-label": "Days" });
    const eodOut = h("div", { style: "margin-top:12px" });
    const runEod = (path, body, label) => (e) => busy(e.currentTarget, async () => {
      const r = await api("POST", path, body); toast(label);
      eodOut.replaceChildren(h("pre", { class: "json" }, JSON.stringify(r, null, 2)));
      const c = await get("/api/staff/ops/clock"); document.getElementById("bank-date").textContent = day(c.business_date);
    });

    return page("System", "The machinery behind the bank, live.", h("button", { class: "quiet", type: "button", onclick: render }, "Refresh"),
      section("DynaStore: the leaderless store", h("a", { href: "/api/docs", target: "_blank", rel: "noopener" }, "API docs"), dynaPanel),
      section("Books", null, recon.__error ? failed(recon) : h("div", { class: recon.balanced ? "notice good" : "notice bad", id: "recon" },
        h("strong", null, recon.balanced ? "The ledger balances" : "The ledger does not balance"),
        recon.balanced ? "Every journal's debits equal its credits, and every account's balance matches its entries."
          : `Sum of all entries: ${recon.entries_total}. ${recon.ledger_mismatches.length} balance and ${recon.hold_mismatches.length} hold mismatches.`)),
      section("Bank calendar", null, clock.__error ? failed(clock) : h("div", { class: "sheet" },
        h("p", null, "The bank's business date is ", h("b", { id: "bank-date" }, day(clock.business_date)), ". End of day posts interest, collects recurring deposits and pays out matured ones."),
        can() ? h("div", { class: "actions", style: "margin-top:14px" },
          h("button", { type: "button", onclick: runEod("/api/staff/ops/eod/run", undefined, "End of day finished") }, "Run end of day"),
          days, h("button", { class: "quiet", type: "button", onclick: (e) => runEod("/api/staff/ops/eod/fast-forward", { days: parseInt(days.value, 10) || 1 }, "Calendar moved forward")(e) }, "Fast-forward this many days"))
          : h("p", { class: "muted small", style: "margin-top:8px" }, "Only an administrator can run end of day."), eodOut)),
      section("Stream analytics", h("span", { class: "muted small" }, "Per-minute windows from the Flink job"), stats.__error ? failed(stats)
        : stats.windows.length ? genericTable(stats.windows) : empty("No windows yet", "The Flink job on Dataproc writes here. It doesn't run in the local stack.")),
      section("Rules in force", null,
        h("details", { class: "sheet" }, h("summary", null, h("b", null, "Risk rules and score bands")), h("pre", { class: "json", style: "margin-top:12px" }, JSON.stringify(rules, null, 2))),
        h("details", { class: "sheet", style: "margin-top:12px" }, h("summary", null, h("b", null, "Rate-limit policies")), h("pre", { class: "json", style: "margin-top:12px" }, JSON.stringify(limits, null, 2)))));
  }

  render();
})();
