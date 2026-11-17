import { useMemo, useState } from "react";
import { motion } from "motion/react";
import { spring } from "@/lib/motion";
import { Empty, PageHead } from "@/components/AppShell";
import { PaymentRow, PaymentSheet } from "@/components/Payment";
import { Card, Skeleton } from "@/components/ui/card";
import { usePayments } from "@/lib/query";
import type { Payment } from "@/lib/types";
import { cn, day, inr } from "@/lib/utils";

const FILTERS = [["all", "All"], ["COMPLETED", "Paid"], ["pending", "Needs you"], ["DECLINED", "Declined"]] as const;

export default function Activity() {
  const q = usePayments(100);
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("all");
  const [opened, setOpened] = useState<Payment | null>(null);
  const list = useMemo(() => (q.data || []).filter((p) =>
    filter === "all" ? true : filter === "pending" ? ["CHALLENGE", "HELD", "PROCESSING"].includes(p.status) : p.status === filter), [q.data, filter]);
  const groups = useMemo(() => {
    const g: [string, Payment[]][] = [];
    for (const p of list) { const d = day(p.created_at); const last = g[g.length - 1]; if (last && last[0] === d) last[1].push(p); else g.push([d, [p]]); }
    return g;
  }, [list]);
  const spent = (q.data || []).filter((p) => p.status === "COMPLETED").reduce((t, p) => t + p.amount, 0);

  return (
    <div>
      <PageHead title="Activity" lead={q.data?.length ? <>You've sent <b className="text-ink num">{inr(spent)}</b> across your last {q.data.length} payments.</> : undefined} />
      <div className="mb-5 flex gap-1 overflow-x-auto rounded-full bg-surface p-1 no-scrollbar sm:inline-flex" role="tablist" aria-label="Filter payments">
        {FILTERS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={filter === k} type="button" onClick={() => setFilter(k)}
            className={cn("press relative shrink-0 grow rounded-full px-4 py-2 text-sm font-semibold transition-colors duration-fast", filter === k ? "text-on-night" : "text-ink-2 hover:text-ink")}>
            {filter === k && <motion.span layoutId="activity-pill" className="absolute inset-0 rounded-full bg-night" transition={spring.snappy} />}
            <span className="relative">{label}</span>
          </button>
        ))}
      </div>
      {q.isLoading ? <Skeleton className="h-96 rounded-card" /> : groups.length ? (
        <motion.div key={filter} className="space-y-6 lg:max-w-3xl" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={spring.page}>
          {groups.map(([d, ps]) => (
            <div key={d}>
              <p className="mb-2 px-1 text-[13px] font-semibold text-muted">{d}</p>
              <Card className="overflow-hidden"><ul className="divide-y divide-line">{ps.map((p) => <PaymentRow key={p.txn_id} p={p} onOpen={setOpened} />)}</ul></Card>
            </div>
          ))}
        </motion.div>
      ) : <Empty title={filter === "all" ? "No payments yet" : "No payments match this filter"} text="Payments you make show up here, with the checks each one went through." />}
      <PaymentSheet p={opened} onClose={() => setOpened(null)} />
    </div>
  );
}
