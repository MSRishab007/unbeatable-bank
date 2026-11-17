import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { KeyRound, ShieldAlert, Receipt, Bell, Check } from "lucide-react";
import { Empty, PageHead } from "@/components/AppShell";
import { Card, Skeleton } from "@/components/ui/card";
import { api, get } from "@/lib/api";
import { queryClient } from "@/lib/query";
import type { Notification } from "@/lib/types";
import { spring } from "@/lib/motion";
import { cn, when } from "@/lib/utils";

const KIND: Record<string, { icon: typeof Bell; tone: string }> = {
  OTP: { icon: KeyRound, tone: "bg-sun-soft text-warn" },
  SECURITY: { icon: ShieldAlert, tone: "bg-danger-soft text-danger" },
  FRAUD: { icon: ShieldAlert, tone: "bg-danger-soft text-danger" },
  TXN: { icon: Receipt, tone: "bg-success-soft text-success-text" },
};

export default function Inbox() {
  const q = useQuery<Notification[]>({ queryKey: ["inbox"], queryFn: () => get("/api/notifications?limit=50"), refetchInterval: 12_000 });
  const read = async (n: Notification) => {
    await api("POST", `/api/notifications/${n.id}/read`);
    queryClient.setQueryData<Notification[]>(["inbox"], (old) => old?.map((x) => (x.id === n.id ? { ...x, read: true } : x)));
  };
  return (
    <div>
      <PageHead title="Inbox" lead="Confirmation codes, receipts and security alerts. They also go out by email." />
      {q.isLoading ? <Skeleton className="h-96 rounded-card" /> : q.data?.length ? (
        <Card className="overflow-hidden lg:max-w-3xl">
          <ul className="divide-y divide-line">
            {q.data.map((n, i) => {
              const k = KIND[n.type] || { icon: Bell, tone: "bg-bg text-ink-2" };
              const code = n.type === "OTP" ? n.body.match(/\b(\d{6})\b/)?.[1] : undefined;
              return (
                <motion.li key={n.id} data-type={n.type} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ ...spring.page, delay: Math.min(i, 10) * 0.04 }}
                  className={cn("relative flex gap-3.5 px-4 py-4 sm:px-5", !n.read && "unread")}>
                  {!n.read && <span aria-hidden className="absolute top-0 bottom-0 left-0 w-1 bg-accent" />}
                  <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-full", k.tone)}><k.icon className="size-5" /></span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-3">
                      <p className={cn(n.read ? "font-medium text-ink-2" : "font-bold text-ink")}>{n.title}{!n.read && <span className="sr-only"> (unread)</span>}</p>
                      <span className="shrink-0 text-xs text-muted">{when(n.created_at)}</span>
                    </div>
                    <p className="mt-0.5 text-[15px] leading-relaxed text-ink-2">{n.body}</p>
                    {code && <p className="mt-2 inline-block rounded-chip bg-sun px-3 py-1.5 font-display text-lg font-semibold tracking-[0.3em] text-sun-ink num">{code}</p>}
                  </div>
                  {!n.read && (
                    <button type="button" aria-label={`Mark "${n.title}" as read`} onClick={() => read(n)} className="press self-start rounded-full p-2 text-muted hover:bg-bg hover:text-ink"><Check className="size-4" /></button>
                  )}
                </motion.li>
              );
            })}
          </ul>
        </Card>
      ) : <Empty title="Nothing here yet" text="You'll hear from us when money moves or something needs your attention." />}
    </div>
  );
}
