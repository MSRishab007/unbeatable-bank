// Sustained load test: makes the Horizontal Pod Autoscalers scale out.
//
//   k6 run -e BASE_URL=http://<public-ip> loadtest/payments.js
//   (or: make loadtest)
//
// What it does for ~10 minutes:
//   - "pay":    real payments through Gateway -> Intake -> Rate limit / Risk / Ledger
//   - "browse": dashboard traffic (accounts, balances, payment history)
//
// Watch the scale-out while it runs:
//   kubectl -n bank get hpa -w          (or the "HPA replicas" panel in Grafana)
//
// Options (-e NAME=value):
//   CUSTOMERS  customers created in setup            (default 150; setup takes about a minute)
//   PAY_RPS    payments per second at the peak       (default CUSTOMERS / 15, stays under rate limits)
//   BROWSE_RPS read requests per second at the peak  (default 120)
//   DURATION   length of the steady phase            (default 6m)
//   ADMIN_PASSWORD staff password                    (default: the dev one)
//
// The gateway must run with ALLOW_SIM_HEADERS=true (each simulated customer gets its own IP).

import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";
import exec from "k6/execution";

const BASE = (__ENV.BASE_URL || "http://localhost:8080").replace(/\/$/, "");
const CUSTOMERS = parseInt(__ENV.CUSTOMERS || "150");
// 4 payments a minute per customer: under the rate limit (10/min) and the velocity risk rule (5/min).
const PAY_RPS = parseInt(__ENV.PAY_RPS || String(Math.max(1, Math.floor(CUSTOMERS / 15))));
const BROWSE_RPS = parseInt(__ENV.BROWSE_RPS || "120");
const DURATION = __ENV.DURATION || "6m";
const RAMP = __ENV.RAMP || "2m";
const PASSWORD = "loadtest-pass-1";

const completed = new Counter("payments_completed");
const declined = new Counter("payments_declined");
const waiting = new Counter("payments_waiting");

// A declined payment (422) or one waiting for an OTP (202) is a correct answer, not a failure.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 422));

export const options = {
  setupTimeout: "10m",
  scenarios: {
    pay: {
      executor: "ramping-arrival-rate",
      exec: "pay",
      startRate: 1,
      timeUnit: "1s",
      preAllocatedVUs: 20,
      maxVUs: 200,
      stages: [
        { target: PAY_RPS, duration: RAMP },
        { target: PAY_RPS, duration: DURATION },
        { target: 0, duration: "1m" },
      ],
    },
    browse: {
      executor: "ramping-arrival-rate",
      exec: "browse",
      startRate: 5,
      timeUnit: "1s",
      preAllocatedVUs: 50,
      maxVUs: 400,
      stages: [
        { target: BROWSE_RPS, duration: RAMP },
        { target: BROWSE_RPS, duration: DURATION },
        { target: 0, duration: "1m" },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.02"],          // under 2% real errors (5xx, timeouts)
    http_req_duration: ["p(95)<1500"],       // 95% of requests under 1.5 s
    payments_completed: ["count>0"],
  },
};

function json(headers) {
  return { headers: Object.assign({ "Content-Type": "application/json" }, headers || {}) };
}

function uuid() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export function setup() {
  const run = Date.now() % 100000000;
  const staff = http.post(`${BASE}/api/staff/login`,
    JSON.stringify({ staff_id: "admin", password: __ENV.ADMIN_PASSWORD || "admin-dev-pass" }), json());
  if (staff.status !== 200) {
    throw new Error(`staff login failed (${staff.status}): is the stack up and ADMIN_PASSWORD right?`);
  }
  const admin = { Authorization: `Bearer ${staff.json("access_token")}` };

  const customers = [];
  for (let i = 0; i < CUSTOMERS; i++) {
    const ip = `10.${200 + (i >> 8)}.${i & 255}.${(run % 200) + 1}`;
    const net = { "X-Sim-Ip": ip };
    const email = `load${run}x${i}@example.com`;
    const reg = http.post(`${BASE}/api/auth/register`, JSON.stringify({
      full_name: `Load User ${i}`, date_of_birth: "1990-01-01",
      phone: `+919${String((run * 1000 + i) % 1000000000).padStart(9, "0")}`, email, password: PASSWORD,
    }), json(net));
    if (reg.status !== 201) {
      throw new Error(`register failed (${reg.status}): ${reg.body}`);
    }
    const id = reg.json("customer_id");
    http.post(`${BASE}/api/staff/kyc/${id}/verify`, null, json(admin));
    const login = http.post(`${BASE}/api/auth/login`, JSON.stringify({
      identifier: email, password: PASSWORD, device_fingerprint: `load-device-${run}-${i}`,
    }), json(net));
    const auth = Object.assign({ Authorization: `Bearer ${login.json("access_token")}` }, net);
    const account = http.post(`${BASE}/api/accounts`, JSON.stringify({ product_type: "SAVINGS" }), json(auth));
    http.post(`${BASE}/api/staff/cash-deposits`,
      JSON.stringify({ account_number: account.json("account_number"), amount: 50000000 }),
      json(Object.assign({ "Idempotency-Key": uuid() }, admin)));
    customers.push({ email, ip, device: `load-device-${run}-${i}`, token: login.json("access_token"),
                     account_id: account.json("account_id"), number: account.json("account_number") });
  }
  console.log(`created ${customers.length} customers; peak ${PAY_RPS} payments/s + ${BROWSE_RPS} reads/s`);
  return { customers, started: Date.now() };
}

// Access tokens last 15 minutes; log in again when a run is longer than that.
const sessions = {};
function auth(data, index) {
  const c = data.customers[index];
  const net = { "X-Sim-Ip": c.ip };
  let s = sessions[index];
  if (!s && Date.now() - data.started < 12 * 60 * 1000) {
    s = sessions[index] = { token: c.token, at: data.started };
  }
  if (!s || Date.now() - s.at > 12 * 60 * 1000) {
    const login = http.post(`${BASE}/api/auth/login`, JSON.stringify({
      identifier: c.email, password: PASSWORD, device_fingerprint: c.device }), json(net));
    s = sessions[index] = { token: login.json("access_token"), at: Date.now() };
  }
  return Object.assign({ Authorization: `Bearer ${s.token}` }, net);
}

export function pay(data) {
  // Walk through the customers in turn so each one stays under its per-minute limit.
  const i = exec.scenario.iterationInTest % data.customers.length;
  const from = data.customers[i];
  const to = data.customers[(i + 1 + Math.floor(Math.random() * (data.customers.length - 1))) % data.customers.length];
  const res = http.post(`${BASE}/api/payments`, JSON.stringify({
    from_account_id: from.account_id, payee: to.number, amount: 1000 + Math.floor(Math.random() * 40000),
  }), Object.assign(json(Object.assign({ "Idempotency-Key": uuid() }, auth(data, i))), { tags: { name: "POST /api/payments" } }));
  check(res, { "payment answered": (r) => [200, 201, 202, 422].includes(r.status) });
  if (res.status === 201) completed.add(1);
  else if (res.status === 202) waiting.add(1);
  else if (res.status === 422) declined.add(1);
}

export function browse(data) {
  const i = Math.floor(Math.random() * data.customers.length);
  const c = data.customers[i];
  const headers = json(auth(data, i));
  const pick = Math.random();
  let res;
  if (pick < 0.4) {
    res = http.get(`${BASE}/api/me/accounts`, Object.assign({ tags: { name: "GET /api/me/accounts" } }, headers));
  } else if (pick < 0.75) {
    res = http.get(`${BASE}/api/payments?limit=20`, Object.assign({ tags: { name: "GET /api/payments" } }, headers));
  } else {
    res = http.get(`${BASE}/api/accounts/${c.account_id}/transactions`,
      Object.assign({ tags: { name: "GET /api/accounts/:id/transactions" } }, headers));
  }
  check(res, { "read ok": (r) => r.status === 200 });
}
