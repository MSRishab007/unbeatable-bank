import { QueryClient, useQuery } from "@tanstack/react-query";
import { api, get } from "./api";
import type { Account, Beneficiary, Me, Notification, Payment, Pins } from "./types";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, retry: (n, e: any) => n < 1 && !(e?.status >= 400 && e?.status < 500), refetchOnWindowFocus: false },
  },
});

export const useMe = () => useQuery<Me>({ queryKey: ["me"], queryFn: () => get("/api/me") });
export const useAccounts = () => useQuery<Account[]>({ queryKey: ["accounts"], queryFn: () => get("/api/me/accounts") });
export const usePayments = (limit = 30) =>
  useQuery<Payment[]>({ queryKey: ["payments", limit], queryFn: () => get(`/api/payments?limit=${limit}`) });
export const useBeneficiaries = () =>
  useQuery<Beneficiary[]>({ queryKey: ["beneficiaries"], queryFn: () => get("/api/me/beneficiaries").catch(() => []) });
export const usePins = () => useQuery<Pins>({ queryKey: ["pins"], queryFn: () => get("/api/auth/pins") });

/** The inbox, checked in the background every 12 s (doesn't keep an idle session awake). */
export const useInbox = (enabled = true) =>
  useQuery<Notification[]>({
    queryKey: ["inbox"],
    queryFn: () => api("GET", "/api/notifications?limit=50", undefined, { background: true }),
    refetchInterval: 12_000,
    enabled,
  });

/** After money moves, everything showing balances or payments is stale. */
export const refreshMoney = () => {
  queryClient.invalidateQueries({ queryKey: ["accounts"] });
  queryClient.invalidateQueries({ queryKey: ["payments"] });
  queryClient.invalidateQueries({ queryKey: ["account"] });
  queryClient.invalidateQueries({ queryKey: ["inbox"] });
};
