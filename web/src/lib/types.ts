export type Account = {
  account_id: string;
  account_number: string;
  product_type: "SAVINGS" | "CURRENT" | "FD" | "RD";
  status: string;
  my_role?: string;
  available_balance: number | null;
  ledger_balance: number | null;
  per_txn_limit: number;
  daily_limit: number;
  pending_per_txn_limit?: number | null;
  pending_daily_limit?: number | null;
  pending_limits_at?: string | null;
  overdraft_limit?: number;
  holders?: { full_name: string; role: string }[];
  opened_at?: string;
  freeze_reason?: string | null;
  alias?: string | null;
};

export type Me = {
  customer_id: string;
  cif_number: string;
  full_name: string;
  email: string;
  phone: string;
  kyc_status: string;
  pan_last4?: string | null;
  daily_transfer_limit: number;
};

export type Payment = {
  txn_id: string;
  status: "COMPLETED" | "DECLINED" | "CHALLENGE" | "HELD" | "PROCESSING" | "CANCELLED" | string;
  amount: number;
  to_display_name?: string | null;
  payee_ref?: string | null;
  to_account_number?: string | null;
  from_account_id?: string;
  note?: string | null;
  created_at: string;
  decline_code?: string | null;
  decline_message?: string | null;
  risk_score?: number | null;
  risk_decision?: string | null;
  risk_reasons?: string[];
  replayed?: boolean;
  channel?: string;
  message?: string;
};

export type Entry = {
  entry_id?: string;
  created_at: string;
  description?: string | null;
  txn_type: string;
  amount: number;
  balance_after: number;
};

export type Beneficiary = {
  beneficiary_id: string;
  nickname?: string | null;
  payee_name: string;
  payee_account_number: string;
  in_cooling_period?: boolean;
  cooling_until?: string;
};

export type Notification = { id: string; type: string; title: string; body: string; read: boolean; created_at: string };

export type Deposit = {
  deposit_id: string;
  product: "FD" | "RD";
  principal: number;
  annual_rate: number;
  tenure_months: number;
  start_date: string;
  maturity_date: string;
  status: string;
  balance: number;
  projected_maturity: number;
  payout_amount?: number | null;
  installments_paid?: number;
  installments_missed?: number;
};

export type Rates = {
  business_date: string;
  rates: { product: "FD" | "RD"; min_days: number; max_days: number; annual_rate: number }[];
};

export type Quote = {
  annual_rate: number;
  total_deposited: number;
  interest: number;
  maturity_amount: number;
  maturity_date: string;
};

export type PinState = { set: boolean; locked?: boolean; locked_until?: string | null };
export type Pins = { PAYMENT: PinState; APP: PinState };

export const PRODUCT: Record<string, string> = {
  SAVINGS: "Savings",
  CURRENT: "Current",
  FD: "Fixed deposit",
  RD: "Recurring deposit",
};
export const payable = (a: Account) => a.product_type === "SAVINGS" || a.product_type === "CURRENT";
