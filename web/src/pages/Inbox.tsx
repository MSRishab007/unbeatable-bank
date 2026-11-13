import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { KeyRound, ShieldAlert, Receipt, Bell, Check } from "lucide-react";
import { Empty, PageHead } from "@/components/AppShell";
import { Card, Skeleton } from "@/components/ui/card";
import { api, get } from "@/lib/api";
import { queryClient } from "@/lib/query";
import type { Notification } from "@/lib/types";
import { cn, when } from "@/lib/utils";

const KIND: Record<string, { icon: typeof Bell; tone: string }> = {
  OTP: { icon: KeyRound, tone: "bg-volt/30 text-[#7a5a00]" },
  SECURITY: { icon: ShieldAlert, tone: "bg-bad-bg text-danger" },
  FRAUD: { icon: ShieldAlert, tone: "bg-bad-bg text-danger" },
  TXN: { icon: Receipt, tone: "bg-good-bg text-go-deep" },
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
      {q.isLoading ? <Skeleton className="h-96" /> : q.data?.length ? (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-line/70">
            {q.data.map((n, i) => {
              const k = KIND[n.type] || { icon: Bell, tone: "bg-mist text-ink-2" };
              const code = n.type === "OTP" ? n.body.match(/\b(\d{6})\b/)?.[1] : undefined;
              return (
                <motion.li key={n.id} data-type={n.type} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: Math.min(i, 10) * 0.03 }}
                  className={cn("flex gap-3.5 px-4 py-4 sm:px-5", !n.read && "unread bg-cobalt/[0.03]")}>
                  <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-full", k.tone)}><k.icon className="size-5" /></span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-3">
                      <p className={cn("font-semibold", !n.read && "text-ink")}>{n.title}</p>
                      <span className="shrink-0 text-xs text-muted">{when(n.created_at)}</span>
                    </div>
                    <p className="mt-0.5 text-[15px] leading-relaxed text-ink-2">{n.body}</p>
                    {code && <p className="mt-2 inline-block rounded-xl bg-mist px-3 py-1.5 font-display text-lg font-semibold tracking-[0.3em] num">{code}</p>}
                  </div>
                  {!n.read && (
                    <button type="button" aria-label="Mark read" onClick={() => read(n)} className="self-start rounded-full p-2 text-muted hover:bg-mist hover:text-ink"><Check className="size-4" /></button>
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
