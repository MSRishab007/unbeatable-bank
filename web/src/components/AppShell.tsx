import { useEffect, useLayoutEffect, useRef } from "react";
import { NavLink, Outlet, useLocation, Link } from "react-router-dom";
import { LayoutGroup, motion } from "motion/react";
import { ArrowLeftRight, Bell, Home, Lock, LogOut, PiggyBank, Send, UserRound } from "lucide-react";
import { toast } from "sonner";
import { Logo } from "./Logo";
import { useInbox } from "@/lib/query";
import { lockNow, signOut, startIdleWatch } from "@/lib/auth";
import { go } from "@/lib/nav";
import { useSession } from "@/lib/session";
import { spring } from "@/lib/motion";
import { cn, initials } from "@/lib/utils";

const NAV = [
  { to: "/home", label: "Home", icon: Home },
  { to: "/activity", label: "Activity", icon: ArrowLeftRight },
  { to: "/deposits", label: "Deposits", icon: PiggyBank },
  { to: "/inbox", label: "Inbox", icon: Bell },
  { to: "/profile", label: "Profile", icon: UserRound },
];
const PAY = { to: "/pay", label: "Pay", icon: Send };
/** Phone tab bar: two tabs, the raised Pay button, two tabs. Inbox is the bell in the top bar. */
const TABS = [NAV[0]!, NAV[1]!, PAY, NAV[2]!, NAV[4]!];

function useInboxToasts() {
  const { data } = useInbox();
  const seen = useRef<Set<string> | null>(null);
  const { pathname } = useLocation();
  useEffect(() => {
    if (!data) return;
    const fresh = seen.current ? data.filter((n) => !seen.current!.has(n.id)) : [];
    seen.current = new Set(data.map((n) => n.id));
    if (fresh.length && pathname !== "/inbox") {
      toast(fresh[0]!.title, { description: fresh[0]!.body.slice(0, 120), action: { label: "Open", onClick: () => go("/inbox") } });
    }
  }, [data, pathname]);
  return data?.filter((n) => !n.read).length ?? 0;
}

/** Home and an account page share the account card, so that pair skips the page fade and lets the card travel. */
const sharesCard = (a: string, b: string) => (a === "/home" && b.startsWith("/accounts/")) || (b === "/home" && a.startsWith("/accounts/"));

export function AppShell() {
  const session = useSession((s) => s.session);
  const unread = useInboxToasts();
  const { pathname } = useLocation();
  const previous = useRef(pathname);
  const morph = sharesCard(previous.current, pathname);
  useEffect(() => {
    previous.current = pathname;
  }, [pathname]);
  useEffect(() => {
    const stop = startIdleWatch();
    return () => stop();
  }, []);
  // Before paint, so a card that travels between pages is measured where it will really be.
  useLayoutEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
      {/* ------------------------------------------------ desktop: side rail */}
      <aside className="sticky top-0 hidden h-dvh flex-col bg-surface px-4 py-6 lg:flex">
        <Link to="/home" className="rounded-chip px-2"><Logo size="sm" /></Link>
        <NavLink to={PAY.to} className={({ isActive }) => cn(
          "press mt-8 flex h-12 items-center justify-center gap-2 rounded-full text-[15px] font-semibold",
          isActive ? "bg-accent-hover text-accent-ink" : "bg-accent text-accent-ink shadow-pay hover:bg-accent-hover")}>
          <Send className="size-[18px]" /> Pay someone
        </NavLink>
        <nav aria-label="Main" className="mt-6 space-y-1">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => cn(
              "press-row relative flex items-center gap-3 rounded-full px-4 py-2.5 text-[15px] font-semibold",
              isActive ? "text-on-night" : "text-ink-2 hover:bg-bg hover:text-ink",
            )}>
              {({ isActive }) => (
                <>
                  {isActive && <motion.span layoutId="rail-pill" className="absolute inset-0 rounded-full bg-night" transition={spring.snappy} />}
                  <Icon className="relative size-5" />
                  <span className="relative">{label}</span>
                  {to === "/inbox" && unread > 0 && (
                    <span className="relative ml-auto rounded-full bg-accent px-2 py-0.5 text-xs text-accent-ink num" id="unread">{unread}</span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-1 border-t border-line pt-4">
          <div className="flex items-center gap-3 px-2 py-2">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-sun text-sm font-bold text-sun-ink">{initials(session?.name)}</span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{session?.name}</p>
              <p className="truncate text-xs text-muted">{session?.email}</p>
            </div>
          </div>
          <button type="button" id="lock-now" onClick={() => lockNow()} className="press-row flex w-full items-center gap-3 rounded-full px-4 py-2.5 text-[15px] font-semibold text-ink-2 hover:bg-bg hover:text-ink">
            <Lock className="size-5" /> Lock
          </button>
          <button type="button" onClick={() => signOut()} className="press-row flex w-full items-center gap-3 rounded-full px-4 py-2.5 text-[15px] font-semibold text-ink-2 hover:bg-bg hover:text-ink">
            <LogOut className="size-5" /> Sign out
          </button>
        </div>
      </aside>

      {/* ------------------------------------------------ phone: top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between bg-bg/90 px-gutter py-2.5 backdrop-blur-md sm:px-6 lg:hidden">
        <Link to="/home" aria-label="UNBank, home" className="rounded-chip"><Logo size="sm" short /></Link>
        <div className="flex items-center gap-1">
          <Link to="/inbox" aria-label={`Inbox${unread ? `, ${unread} unread` : ""}`} className="press relative rounded-full p-2.5 text-ink-2 hover:bg-ink/6">
            <Bell className="size-5" />
            {unread > 0 && <span className="absolute top-1 right-1 flex size-[18px] items-center justify-center rounded-full bg-accent text-[10px] font-bold text-accent-ink">{unread > 9 ? "9+" : unread}</span>}
          </Link>
          <button type="button" aria-label="Lock the app" onClick={() => lockNow()} className="press rounded-full p-2.5 text-ink-2 hover:bg-ink/6"><Lock className="size-5" /></button>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-[1180px] px-gutter pt-3 pb-[calc(var(--spacing-tabbar)+2.5rem)] sm:px-6 lg:px-10 lg:pt-10 lg:pb-16">
        <LayoutGroup>
          {/* Enter-only: an exit animation around <Outlet> can leave the next page stuck invisible. */}
          <motion.div key={pathname} initial={morph ? false : { opacity: 0, y: 18, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={spring.page}>
            <Outlet />
          </motion.div>
        </LayoutGroup>
      </main>

      {/* ------------------------------------------------ phone: tab bar with the raised Pay button */}
      <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 bg-surface pb-[env(safe-area-inset-bottom)] shadow-[0_-1px_0_var(--color-line)] lg:hidden">
        <div className="mx-auto grid h-[4.25rem] max-w-md grid-cols-5 items-stretch px-1">
          {TABS.map(({ to, label, icon: Icon }) =>
            to === PAY.to ? (
              <NavLink key={to} to={to} aria-label="Pay" className="group flex flex-col items-center">
                {({ isActive }) => (
                  <>
                    <motion.span whileTap={{ scale: 0.9 }} transition={spring.bouncy}
                      className={cn("-mt-6 flex size-[60px] items-center justify-center rounded-full border-4 border-bg text-accent-ink", isActive ? "bg-accent-hover" : "bg-accent shadow-pay")}>
                      <Icon className="size-6" />
                    </motion.span>
                    <span className={cn("mt-0.5 text-[11px] font-semibold", isActive ? "text-ink" : "text-muted")}>Pay</span>
                  </>
                )}
              </NavLink>
            ) : (
              <NavLink key={to} to={to} className={({ isActive }) => cn("press relative flex flex-col items-center justify-center gap-1 text-[11px] font-semibold", isActive ? "text-ink" : "text-muted")}>
                {({ isActive }) => (
                  <>
                    {isActive && <motion.span layoutId="tab-dot" className="absolute top-0 h-[3px] w-8 rounded-full bg-accent" transition={spring.snappy} />}
                    <Icon className="size-[22px]" strokeWidth={isActive ? 2.4 : 2} />
                    {label}
                  </>
                )}
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
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 lg:mb-8">
      <div>
        <h1 className="font-display text-[28px] font-semibold tracking-[-0.03em] sm:text-[36px]">{title}</h1>
        {lead && <p className="mt-1.5 max-w-xl text-[15px] text-muted">{lead}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, action, children, className }: { title: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("mt-9", className)}>
      <div className="mb-3 flex min-h-9 items-center justify-between gap-3">
        <h2 className="text-[17px] font-semibold tracking-tight">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Empty({ title, text, action }: { title: string; text: string; action?: React.ReactNode }) {
  return (
    <div className="rounded-card border-2 border-dashed border-line px-6 py-10 text-center">
      <p className="font-semibold">{title}</p>
      <p className="mx-auto mt-1 max-w-sm text-[15px] text-muted">{text}</p>
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}

/** "See all" and similar text links beside a section title. */
export const sectionLink = "press rounded-full text-sm font-semibold text-accent-text hover:underline underline-offset-4";
