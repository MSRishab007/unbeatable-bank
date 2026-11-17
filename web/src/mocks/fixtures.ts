// Development-only data for `VITE_MOCK=1 npm run dev`. Never part of the production build.
import type { Account, Beneficiary, Deposit, Entry, Me, Notification, Payment } from "@/lib/types";

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

export const me: Me = {
  customer_id: "c0ffee00-1111-4222-8333-444455556666", cif_number: "CIF00018242", full_name: "Asha Rao",
  email: "asha@example.com", phone: "+919812345670", kyc_status: "VERIFIED", pan_last4: "234F", daily_transfer_limit: 20_000_000,
};

export const accounts: Account[] = [
  { account_id: "acc-savings", account_number: "500100018242", product_type: "SAVINGS", status: "ACTIVE", my_role: "PRIMARY",
    available_balance: 4_275_000, ledger_balance: 4_475_000, per_txn_limit: 5_000_000, daily_limit: 10_000_000,
    holders: [{ full_name: "Asha Rao", role: "PRIMARY" }], opened_at: "2026-08-02", alias: "asha@unbeatable" },
  { account_id: "acc-current", account_number: "500200018243", product_type: "CURRENT", status: "ACTIVE", my_role: "JOINT",
    available_balance: 18_640_050, ledger_balance: 18_640_050, per_txn_limit: 20_000_000, daily_limit: 50_000_000,
    holders: [{ full_name: "Asha Rao", role: "PRIMARY" }, { full_name: "Kabir Rao", role: "JOINT" }], opened_at: "2026-09-14" },
];

export const entries: Entry[] = [
  { entry_id: "e1", created_at: ago(40), description: "Ravi Kumar", txn_type: "TRANSFER_OUT", amount: -25_000, balance_after: 4_275_000 },
  { entry_id: "e2", created_at: ago(60 * 26), description: "Meera Shah, rent", txn_type: "TRANSFER_OUT", amount: -700_000, balance_after: 4_300_000 },
  { entry_id: "e3", created_at: ago(60 * 30), description: "Salary, Northwind Labs", txn_type: "TRANSFER_IN", amount: 4_800_000, balance_after: 5_000_000 },
  { entry_id: "e4", created_at: ago(60 * 80), description: "Interest", txn_type: "INTEREST", amount: 12_450, balance_after: 200_000 },
  { entry_id: "e5", created_at: ago(60 * 200), description: "Cash deposit", txn_type: "CASH_DEPOSIT", amount: 187_550, balance_after: 187_550 },
];

export const names: Record<string, string> = {
  "500100020001": "Ravi Kumar", "500100020002": "Meera Shah", "500100020003": "Toit Brewpub", "500100020004": "Imran Qureshi",
  "500100020005": "Sana Verghese",
};

const pay = (id: string, mins: number, to: string, amount: number, status: Payment["status"], extra: Partial<Payment> = {}): Payment => ({
  txn_id: id, status, amount, to_display_name: names[to], payee_ref: to, to_account_number: to, from_account_id: "acc-savings",
  created_at: ago(mins), risk_score: 6, risk_decision: "ALLOW", risk_reasons: [], channel: "TRANSFER", ...extra,
});
export const payments: Payment[] = [
  pay("7c1d2a90-0001", 40, "500100020001", 25_000, "COMPLETED", { note: "Chai and samosas", channel: "QR_PAYMENT" }),
  pay("7c1d2a90-0002", 190, "500100020003", 184_000, "COMPLETED", { note: "Dinner" }),
  pay("7c1d2a90-0003", 60 * 26, "500100020002", 700_000, "COMPLETED", { note: "Rent", risk_score: 31 }),
  pay("7c1d2a90-0004", 60 * 28, "500100020004", 2_000_000, "HELD", { risk_score: 78, risk_decision: "REVIEW", risk_reasons: ["NEW_PAYEE", "HIGH_AMOUNT"] }),
  pay("7c1d2a90-0005", 60 * 50, "500100020005", 9_900_000, "DECLINED", { decline_code: "PER_TXN_LIMIT", decline_message: "That's over your ₹50,000 limit for one payment. Raise the limit on the account page, or send less." }),
  pay("7c1d2a90-0006", 60 * 75, "500100020001", 60_000, "COMPLETED", { note: "Cab" }),
];

export const beneficiaries: Beneficiary[] = [
  { beneficiary_id: "b1", nickname: "Landlord", payee_name: "Meera Shah", payee_account_number: "500100020002" },
  { beneficiary_id: "b2", payee_name: "Ravi Kumar", payee_account_number: "500100020001" },
  { beneficiary_id: "b3", nickname: "Sana", payee_name: "Sana Verghese", payee_account_number: "500100020005", in_cooling_period: true, cooling_until: new Date(Date.now() + 20 * 3_600_000).toISOString() },
];

export const inbox: Notification[] = [
  { id: "n1", type: "OTP", title: "Confirm your payment", body: "Use 654321 to confirm ₹7,000.00 to Meera Shah. It works for five minutes. Never share it.", read: false, created_at: ago(2) },
  { id: "n2", type: "TXN", title: "You paid Ravi Kumar", body: "₹250.00 left your savings account ending 8242. Chai and samosas.", read: false, created_at: ago(40) },
  { id: "n3", type: "SECURITY", title: "New device signed in", body: "Chrome on Windows signed in to your account. If this wasn't you, block everything from Profile.", read: true, created_at: ago(60 * 27) },
  { id: "n4", type: "FRAUD", title: "A payment is under review", body: "₹20,000.00 to Imran Qureshi is paused while an analyst checks it. The money is set aside.", read: true, created_at: ago(60 * 28) },
  { id: "n5", type: "INFO", title: "Your KYC is verified", body: "Both documents were accepted. Your accounts are active.", read: true, created_at: ago(60 * 300) },
];

export const deposits: Deposit[] = [
  { deposit_id: "d1", product: "FD", principal: 5_000_000, annual_rate: 0.0725, tenure_months: 12, start_date: "2026-08-10", maturity_date: "2027-08-10",
    status: "ACTIVE", balance: 5_089_300, projected_maturity: 5_372_400 },
  { deposit_id: "d2", product: "RD", principal: 200_000, annual_rate: 0.068, tenure_months: 24, start_date: "2026-09-01", maturity_date: "2028-09-01",
    status: "ACTIVE", balance: 603_100, projected_maturity: 5_151_200, installments_paid: 3, installments_missed: 0 },
];

export const rates = {
  business_date: new Date().toISOString().slice(0, 10),
  rates: [
    { product: "FD", min_days: 30, max_days: 180, annual_rate: 0.055 }, { product: "FD", min_days: 181, max_days: 364, annual_rate: 0.065 },
    { product: "FD", min_days: 365, max_days: 1095, annual_rate: 0.0725 }, { product: "FD", min_days: 1096, max_days: 3650, annual_rate: 0.07 },
    { product: "RD", min_days: 180, max_days: 364, annual_rate: 0.06 }, { product: "RD", min_days: 365, max_days: 3650, annual_rate: 0.068 },
  ],
};

export const kyc = {
  max_bytes: 5 * 1048576,
  types: [
    { doc_type: "PAN", label: "PAN card", required: true, document: { status: "APPROVED", uploaded_at: ago(60 * 400) } },
    { doc_type: "ADDRESS_PROOF", label: "address proof", required: true, document: { status: "APPROVED", uploaded_at: ago(60 * 399) } },
    { doc_type: "PHOTO", label: "photograph", required: false, document: null },
  ] as any[],
};

export const security = {
  devices: [{ device_id: "dv1", label: "Chrome on Android" }, { device_id: "dv2", label: "Chrome on Windows" }],
  sessions: [
    { session_id: "s1", device_id: "dv1", ip: "49.207.18.4", created_at: ago(12), current: true },
    { session_id: "s2", device_id: "dv2", ip: "103.21.4.77", created_at: ago(60 * 27), current: false },
  ],
  logins: [
    { at: ago(12), result: "SUCCESS", ip: "49.207.18.4" },
    { at: ago(60 * 27), result: "CHALLENGED", ip: "103.21.4.77", new_device: true },
    { at: ago(60 * 90), result: "FAILED", ip: "185.220.101.9", impossible_travel: true },
  ],
};
