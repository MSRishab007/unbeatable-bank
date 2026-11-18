// A stand-in for the bank, for trying the app with no backend and no cloud:
//
//     npm run demo
//
// It answers every /api call the app makes from the fixtures next door. main.tsx loads this
// file only when VITE_MOCK is "1" (demo mode), so none of it reaches the production build.
//
//   Sign in       any email and password ("wrong" as the password fails; an email with "new" asks for a device code, 123456)
//   Payment PIN   123456        App PIN  1234        One-time code  654321
//   Amounts       ₹7,000 is challenged, ₹20,000 is held, over ₹50,000 is declined, anything else goes through
//   Account numbers starting 5001 0002 000x are real payees; anything starting with 9 doesn't exist
import * as fx from "./fixtures";
import type { Payment } from "@/lib/types";

const PAY_PIN = "123456", APP_PIN = "1234", OTP = "654321";
const pins = { PAYMENT: { set: true, locked: false }, APP: { set: true, locked: false } };
const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const fail = (status: number, error: string, message: string, details?: unknown) => json(status, { error, message, details });
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const nameOf = (n: string) => fx.names[n] || (/^9/.test(n) ? null : `Account ${n.slice(-4)}`);
let seq = 100;
const payByMobile = { enabled: false, mobile: "+919876543210", receiving_account_id: null as string | null };

function needPin(req: Request, kind: "PAYMENT" | "APP") {
  const pin = req.headers.get("X-Pin");
  if (!pin) return fail(428, "PIN_REQUIRED", "Enter your PIN.", { kind });
  if (pin !== (kind === "PAYMENT" ? PAY_PIN : APP_PIN)) return fail(403, "PIN_WRONG", "That PIN isn't right. 2 tries left.", { kind, attempts_left: 2 });
  return null;
}

function quote(product: string, amount: number, months: number) {
  const rate = product === "FD" ? (months >= 12 ? 0.0725 : 0.065) : 0.068;
  const total = product === "FD" ? amount : amount * months;
  const interest = Math.round(product === "FD" ? amount * (Math.pow(1 + rate / 4, months / 3) - 1) : amount * months * rate * (months + 1) / 24);
  const d = new Date(); d.setMonth(d.getMonth() + months);
  return { annual_rate: rate, total_deposited: total, interest, maturity_amount: total + interest, maturity_date: d.toISOString().slice(0, 10) };
}

async function handle(req: Request, url: URL): Promise<Response> {
  const m = req.method, p = url.pathname.replace(/^\/api/, "");
  const body: any = m === "GET" || m === "DELETE" ? null : await req.clone().json().catch(() => null);
  const is = (method: string, re: RegExp) => (m === method ? p.match(re) : null);
  let x: RegExpMatchArray | null;

  // ---- sign-in
  if (is("POST", /^\/auth\/login$/)) {
    if (body.password === "wrong") return fail(401, "BAD_CREDENTIALS", "That email or password isn't right. Check them and try again.");
    if (String(body.identifier).includes("new")) return json(202, { challenge_id: "ch1", message: "We sent a code to the phone and email you registered." });
    return json(200, { access_token: "mock-access", refresh_token: "mock-refresh", idle_lock_seconds: 300 });
  }
  if (is("POST", /^\/auth\/login\/verify$/)) {
    return body.code === "123456" ? json(200, { access_token: "mock-access", refresh_token: "mock-refresh", idle_lock_seconds: 300, new_device: true })
      : fail(400, "BAD_CODE", "That code isn't right. Check your email and try again.");
  }
  if (is("POST", /^\/auth\/(register|logout|lock|password\/forgot|password\/reset|contact|contact\/confirm)$/)) return json(200, { customer_id: fx.me.customer_id });
  if (is("POST", /^\/auth\/refresh$/)) return json(200, { access_token: "mock-access" });
  if (is("POST", /^\/auth\/password$/)) return json(200, { other_sessions_ended: 1 });
  if (is("POST", /^\/auth\/unlock$/)) {
    return body.pin === APP_PIN || body.password ? json(200, {}) : fail(403, "PIN_WRONG", "Wrong PIN", { attempts_left: 2 });
  }
  if (is("GET", /^\/auth\/pins$/)) return json(200, pins);
  if (is("PUT", /^\/auth\/pins\//) || is("POST", /^\/auth\/pins\//)) return json(200, {});
  if (is("GET", /^\/auth\/devices$/)) return json(200, fx.security.devices);
  if (is("GET", /^\/auth\/sessions$/)) return json(200, fx.security.sessions);
  if (is("GET", /^\/auth\/login-history$/)) return json(200, fx.security.logins);
  if ((x = is("DELETE", /^\/auth\/sessions\/(.+)$/))) { fx.security.sessions.splice(fx.security.sessions.findIndex((s) => s.session_id === x![1]), 1); return json(200, {}); }

  // ---- customer and accounts
  if (is("GET", /^\/me$/)) return json(200, fx.me);
  if (is("GET", /^\/me\/accounts$/)) return json(200, fx.accounts);
  if (is("POST", /^\/me\/lockdown$/)) return json(200, { accounts_frozen: fx.accounts.map((a) => a.account_number), accounts_failed: [], case_number: "FR-2026-0042" });
  if (is("POST", /^\/accounts$/)) {
    const a = { ...fx.accounts[0]!, account_id: `acc-${++seq}`, account_number: `5001000${18000 + seq}`, product_type: body.product_type, available_balance: 0, ledger_balance: 0, alias: null };
    fx.accounts.push(a);
    return json(201, a);
  }
  if ((x = p.match(/^\/accounts\/([^/]+)(\/.*)?$/))) {
    const a = fx.accounts.find((v) => v.account_id === x![1]);
    if (!a) return fail(404, "NOT_FOUND", "That account doesn't exist.");
    const rest = x[2] || "";
    if (m === "GET" && !rest) return json(200, a);
    if (rest === "/balance") return json(200, { available_balance: a.available_balance, ledger_balance: a.ledger_balance, accrued_interest: a.product_type === "SAVINGS" ? 18_420 : 0 });
    if (rest === "/transactions") return json(200, a.account_id === "acc-savings" ? fx.entries : []);
    if (rest === "/limits") { const no = needPin(req, "PAYMENT"); if (no) return no; Object.assign(a, body); return json(200, a); }
    if (rest === "/alias") { a.alias = `${body.alias}@unbeatable`; return json(200, { alias: a.alias }); }
    if (rest === "/freeze") { a.status = "FROZEN"; a.freeze_reason = body.reason; return json(200, a); }
  }

  // ---- payees
  if ((x = is("GET", /^\/payees\/lookup\/(.+)$/)) || (x = is("GET", /^\/payees\/(.+)$/)) || (x = is("GET", /^\/aliases\/(.+)$/))) {
    const key = decodeURIComponent(x[1]!).replace(/\s/g, "");
    const mobile = /^(\+91|0)?[6-9]\d{9}$/.test(key) && !/^\d{12}$/.test(key);
    const kind = key.includes("@") ? "PAY_ID" : mobile ? "MOBILE" : "ACCOUNT_NUMBER";
    const number = kind === "ACCOUNT_NUMBER" ? key : "500100020001";
    const n = kind === "PAY_ID" ? (key === "ravi@unbeatable" ? "Ravi Kumar" : null) : kind === "MOBILE" ? (key.endsWith("43210") ? "Ravi Kumar" : null) : nameOf(key);
    return n ? json(200, { display_name: n, account_number: number, kind, can_receive: true })
      : fail(404, "NOT_FOUND", kind === "MOBILE" ? "No one receives payments on this mobile number." : "No account matches that.");
  }
  if (is("GET", /^\/me\/pay-by-mobile$/)) return json(200, payByMobile);
  if (is("PUT", /^\/me\/pay-by-mobile$/)) {
    const no = needPin(req, "APP"); if (no) return no;
    Object.assign(payByMobile, { enabled: !!body.enabled, receiving_account_id: body.receiving_account_id ?? null });
    return json(200, payByMobile);
  }
  if (is("GET", /^\/me\/beneficiaries$/)) return json(200, fx.beneficiaries);
  if (is("POST", /^\/me\/beneficiaries$/)) {
    const no = needPin(req, "PAYMENT"); if (no) return no;
    const b = { beneficiary_id: `b${++seq}`, nickname: body.nickname, payee_name: nameOf(body.payee_account_number) || "Unknown", payee_account_number: body.payee_account_number,
      in_cooling_period: true, cooling_until: new Date(Date.now() + 24 * 3_600_000).toISOString() };
    fx.beneficiaries.push(b);
    return json(201, b);
  }
  if ((x = is("DELETE", /^\/me\/beneficiaries\/(.+)$/))) { fx.beneficiaries.splice(fx.beneficiaries.findIndex((b) => b.beneficiary_id === x![1]), 1); return json(204); }

  // ---- payments
  if (is("GET", /^\/payments$/)) return json(200, fx.payments.slice(0, Number(url.searchParams.get("limit") || 30)));
  if (is("POST", /^\/payments$/)) {
    const no = needPin(req, "PAYMENT"); if (no) return no;
    await wait(500);
    const to = String(body.payee);
    const kind = to.includes("@") ? "PAY_ID" : /^\d{12}$/.test(to) ? "ACCOUNT_NUMBER" : "MOBILE";
    const name = kind === "ACCOUNT_NUMBER" ? nameOf(to) || to : "Ravi Kumar";
    const pay: Payment = { txn_id: crypto.randomUUID(), status: "COMPLETED", amount: body.amount, to_display_name: name, payee_ref: to,
      to_account_number: kind === "ACCOUNT_NUMBER" ? to : "500100020001", payee_kind: kind, from_account_id: body.from_account_id, note: body.note, created_at: new Date().toISOString(),
      risk_score: 4, risk_decision: "ALLOW", risk_reasons: [], channel: body.channel };
    if (body.amount > 5_000_000) Object.assign(pay, { status: "DECLINED", decline_code: "PER_TXN_LIMIT", risk_score: null,
      decline_message: "That's over your ₹50,000 limit for one payment. Raise the limit on the account page, or send less." });
    else if (body.amount === 700_000) Object.assign(pay, { status: "CHALLENGE", risk_score: 58, risk_decision: "CHALLENGE", risk_reasons: ["NEW_DEVICE", "NEW_PAYEE"] });
    else if (body.amount === 2_000_000) Object.assign(pay, { status: "HELD", risk_score: 81, risk_decision: "REVIEW", risk_reasons: ["HIGH_AMOUNT", "UNUSUAL_HOUR"] });
    else { const a = fx.accounts.find((v) => v.account_id === body.from_account_id); if (a) { a.available_balance! -= body.amount; a.ledger_balance! -= body.amount; } }
    fx.payments.unshift(pay);
    return json(pay.status === "DECLINED" ? 422 : 201, pay);
  }
  if ((x = is("POST", /^\/payments\/([^/]+)\/confirm$/))) {
    const pay = fx.payments.find((v) => v.txn_id === x![1])!;
    if (body.otp !== OTP) return fail(400, "BAD_CODE", "That code isn't right. Check your email or phone and try again.");
    Object.assign(pay, { status: "COMPLETED", risk_decision: "ALLOW" });
    return json(200, pay);
  }
  if ((x = is("POST", /^\/payments\/([^/]+)\/deny$/))) {
    const pay = fx.payments.find((v) => v.txn_id === x![1])!;
    Object.assign(pay, { status: "DECLINED", decline_code: "CUSTOMER_DENIED", decline_message: "You told us this wasn't you. The payment was cancelled." });
    return json(200, pay);
  }
  if (is("POST", /^\/reports$/)) return json(201, { case_number: "FR-2026-0043" });

  // ---- inbox
  if (is("GET", /^\/notifications$/)) return json(200, fx.inbox);
  if ((x = is("POST", /^\/notifications\/([^/]+)\/read$/))) { const n = fx.inbox.find((v) => v.id === x![1]); if (n) n.read = true; return json(200, {}); }

  // ---- deposits
  if (is("GET", /^\/deposits\/rates$/)) return json(200, fx.rates);
  if (is("GET", /^\/deposits\/quote$/)) {
    const q = url.searchParams;
    return json(200, quote(q.get("product")!, Number(q.get("amount")), Number(q.get("tenure_months"))));
  }
  if (is("GET", /^\/deposits$/)) return json(200, fx.deposits);
  if ((x = is("POST", /^\/deposits\/(fd|rd)$/))) {
    const no = needPin(req, "APP"); if (no) return no;
    const product = x[1]!.toUpperCase() as "FD" | "RD";
    const q = quote(product, body.amount, body.tenure_months);
    fx.deposits.unshift({ deposit_id: `d${++seq}`, product, principal: body.amount, annual_rate: q.annual_rate, tenure_months: body.tenure_months,
      start_date: new Date().toISOString().slice(0, 10), maturity_date: q.maturity_date, status: "ACTIVE", balance: body.amount, projected_maturity: q.maturity_amount, installments_paid: 1, installments_missed: 0 });
    return json(201, { status: "ACTIVE", annual_rate: q.annual_rate });
  }
  if ((x = is("GET", /^\/deposits\/([^/]+)\/closure-quote$/))) {
    const d = fx.deposits.find((v) => v.deposit_id === x![1])!;
    return json(200, { held_days: 98, booked_rate: d.annual_rate, closure_rate: d.annual_rate - 0.02, deposited: d.principal, maturity_amount_if_kept: d.projected_maturity, payout_amount: d.balance - 20_000 });
  }
  if ((x = is("POST", /^\/deposits\/([^/]+)\/close$/))) {
    const no = needPin(req, "APP"); if (no) return no;
    const d = fx.deposits.find((v) => v.deposit_id === x![1])!;
    Object.assign(d, { status: "CLOSED", payout_amount: d.balance - 20_000 });
    return json(200, d);
  }

  // ---- KYC
  if (is("GET", /^\/kyc\/documents$/)) return json(200, fx.kyc);
  if (is("POST", /^\/kyc\/documents$/)) {
    const t = fx.kyc.types.find((v) => v.doc_type === url.searchParams.get("doc_type"));
    if (t) t.document = { status: "PENDING", uploaded_at: new Date().toISOString() };
    return json(201, {});
  }
  return fail(404, "NOT_FOUND", `The mock bank has no answer for ${m} ${p}.`);
}

export function installMockBank() {
  const real = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const req = new Request(input, init);
    const url = new URL(req.url);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return real(input, init);
    await wait(180 + Math.random() * 220);
    return handle(req, url);
  };
  showDemoCard();
  console.info("[mock bank] answering /api from src/mocks. Payment PIN 123456, app PIN 1234, one-time code 654321.");
}

/** A small card in the corner, so nobody mistakes demo data for the real bank or has to look up the PINs. */
function showDemoCard() {
  const card = document.createElement("details");
  card.id = "demo-card";
  card.style.cssText = "position:fixed;left:12px;bottom:calc(var(--spacing-tabbar) + 12px);z-index:70;max-width:270px;font:500 13px/1.45 var(--font-sans);" +
    "background:var(--color-night);color:var(--color-on-night);border-radius:var(--radius-card);box-shadow:var(--shadow-float)";
  card.innerHTML = `<summary style="cursor:pointer;padding:8px 14px;font-weight:700;list-style:none">Demo data <span style="color:var(--color-sun)">· tap for PINs</span></summary>
    <div style="padding:2px 14px 12px;color:var(--color-on-night-muted)">
      Nothing here reaches a real bank.<br>
      <b style="color:var(--color-on-night)">Sign in:</b> any email and password<br>
      <b style="color:var(--color-on-night)">Payment PIN:</b> 123456<br>
      <b style="color:var(--color-on-night)">App PIN:</b> 1234<br>
      <b style="color:var(--color-on-night)">One-time code:</b> 654321<br>
      <b style="color:var(--color-on-night)">Pay to:</b> 5001 0002 0001<br>
      ₹7,000 asks for a code, ₹20,000 is held, over ₹50,000 is declined.
    </div>`;
  document.body.appendChild(card);
}
