import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation, Link } from "react-router-dom";
import { motion } from "motion/react";
import { Bell, Home, ArrowLeftRight, PiggyBank, UserRound, Lock, LogOut, Send } from "lucide-react";
import { toast } from "sonner";
import { Logo, Mark } from "./Logo";
import { useInbox } from "@/lib/query";
import { lockNow, signOut, startIdleWatch } from "@/lib/auth";
import { useSession } from "@/lib/session";
import { cn, initials } from "@/lib/utils";

const NAV = [
  { to: "/home", label: "Home", icon: Home },
  { to: "/pay", label: "Pay", icon: Send },
  { to: "/activity", label: "Activity", icon: ArrowLeftRight },
  { to: "/deposits", label: "Deposits", icon: PiggyBank },
  { to: "/inbox", label: "Inbox", icon: Bell },
  { to: "/profile", label: "Profile", icon: UserRound },
];

function useInboxToasts() {
  const { data } = useInbox();
  const seen = useRef<Set<string> | null>(null);
  const { pathname } = useLocation();
  useEffect(() => {
    if (!data) return;
    const fresh = seen.current ? data.filter((n) => !seen.current!.has(n.id)) : [];
    seen.current = new Set(data.map((n) => n.id));
    if (fresh.length && pathname !== "/inbox") {
      toast(fresh[0]!.title, { description: fresh[0]!.body.slice(0, 120), action: { label: "Open", onClick: () => (location.hash = "/inbox") } });
    }
  }, [data, pathname]);
  return data?.filter((n) => !n.read).length ?? 0;
}

export function AppShell() {
  const session = useSession((s) => s.session);
  const unread = useInboxToasts();
  const { pathname } = useLocation();
  useEffect(() => {
    const stop = startIdleWatch();
    return () => stop();
  }, []);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[256px_1fr]">
      {/* ------------------------------------------------ desktop rail */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r border-line bg-surface px-4 py-6 lg:flex">
        <Link to="/home" className="px-3"><Logo size="sm" /></Link>
        <nav aria-label="Main" className="mt-10 space-y-1">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => cn(
              "relative flex items-center gap-3 rounded-2xl px-3 py-2.5 text-[15px] font-semibold transition-colors",
              isActive ? "text-cobalt" : "text-ink-2 hover:bg-mist hover:text-ink",
            )}>
              {({ isActive }) => (
                <>
                  {isActive && <motion.span layoutId="rail-pill" className="absolute inset-0 rounded-2xl bg-cobalt/8" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
                  <Icon className="relative size-5" />
                  <span className="relative">{label}</span>
                  {to === "/inbox" && unread > 0 && (
                    <span className="relative ml-auto rounded-full bg-cobalt px-2 py-0.5 text-xs text-white num" id="unread">{unread}</span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-1 border-t border-line pt-4">
          <div className="flex items-center gap-3 px-3 py-2">
            <span className="flex size-9 items-center justify-center rounded-full bg-volt text-sm font-bold text-ink">{initials(session?.name)}</span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{session?.name}</p>
              <p className="truncate text-xs text-muted">{session?.email}</p>
            </div>
          </div>
          <button type="button" id="lock-now" onClick={() => lockNow()} className="flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-[15px] font-semibold text-ink-2 hover:bg-mist">
            <Lock className="size-5" /> Lock
          </button>
          <button type="button" onClick={() => signOut()} className="flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-[15px] font-semibold text-ink-2 hover:bg-mist">
            <LogOut className="size-5" /> Sign out
          </button>
        </div>
      </aside>

      {/* ------------------------------------------------ phone top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line/60 bg-mist/85 px-4 py-3 backdrop-blur-md lg:hidden">
        <Link to="/home" aria-label="Home"><Mark className="size-9" title={null} /></Link>
        <div className="flex items-center gap-1">
          <Link to="/inbox" aria-label={`Inbox${unread ? `, ${unread} unread` : ""}`} className="relative rounded-full p-2.5 text-ink-2 hover:bg-ink/5">
            <Bell className="size-5" />
            {unread > 0 && <span className="absolute top-1.5 right-1.5 flex size-4 items-center justify-center rounded-full bg-cobalt text-[10px] font-bold text-white">{unread > 9 ? "9+" : unread}</span>}
          </Link>
          <button type="button" aria-label="Lock" onClick={() => lockNow()} className="rounded-full p-2.5 text-ink-2 hover:bg-ink/5"><Lock className="size-5" /></button>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-[1080px] px-4 pt-5 pb-32 sm:px-6 lg:px-10 lg:pt-10 lg:pb-16">
        {/* Enter-only: an exit animation around <Outlet> can leave the next page stuck invisible. */}
        <motion.div key={pathname} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
          <Outlet />
        </motion.div>
      </main>

      {/* ------------------------------------------------ phone tab bar */}
      <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md lg:hidden">
        <div className="mx-auto grid max-w-md grid-cols-5 items-end px-2">
          {[NAV[0]!, NAV[2]!, NAV[1]!, NAV[3]!, NAV[5]!].map(({ to, label, icon: Icon }) =>
            to === "/pay" ? (
              <NavLink key={to} to={to} className="flex flex-col items-center gap-1 pb-2" aria-label="Pay">
                {({ isActive }) => (
                  <>
                    <span className={cn("-mt-5 flex size-14 items-center justify-center rounded-full text-white shadow-[0_10px_24px_-8px_rgba(45,42,230,.7)] transition-transform", isActive ? "bg-cobalt-deep scale-95" : "bg-cobalt")}>
                      <Icon className="size-6" />
                    </span>
                    <span className="text-[11px] font-semibold text-ink-2">Pay</span>
                  </>
                )}
              </NavLink>
            ) : (
              <NavLink key={to} to={to} className={({ isActive }) => cn("flex flex-col items-center gap-1 py-2.5 text-[11px] font-semibold", isActive ? "text-cobalt" : "text-muted")}>
                <Icon className="size-[22px]" />
                {label}
              </NavLink>
            ),
          )}
        </div>
      </nav>
    </div>
  );
}

export function PageHead({ title, lead, actions }: { title: React.ReactNode; lead?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-display text-[28px] font-semibold tracking-[-0.025em] sm:text-[34px]">{title}</h1>
        {lead && <p className="mt-1.5 max-w-xl text-[15px] text-muted">{lead}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, action, children, className }: { title: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("mt-9", className)}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[17px] font-semibold tracking-tight">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Empty({ title, text, action }: { title: string; text: string; action?: React.ReactNode }) {
  return (
    <div className="rounded-[var(--radius-card)] border border-dashed border-line bg-surface/60 px-6 py-10 text-center">
      <p className="font-semibold">{title}</p>
      <p className="mx-auto mt-1 max-w-sm text-[15px] text-muted">{text}</p>
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}
