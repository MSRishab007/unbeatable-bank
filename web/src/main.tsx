import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { Toaster } from "sonner";
import "./index.css";
import { queryClient } from "./lib/query";
import { useSession } from "./lib/session";
import "./lib/auth";
import { Prompts } from "./components/Prompts";
import { AppShell } from "./components/AppShell";
import Landing from "./pages/Landing";
import { Register, SignIn } from "./pages/Auth";
import Home from "./pages/Home";
import AccountPage from "./pages/Account";
import Pay from "./pages/Pay";
import Receive from "./pages/Receive";
import Activity from "./pages/Activity";
import Inbox from "./pages/Inbox";
import Deposits from "./pages/Deposits";
import Profile from "./pages/Profile";

/** Signed-in customers only; anyone else goes to sign in and comes back afterwards. */
function Customer() {
  const session = useSession((s) => s.session);
  const loc = useLocation();
  if (!session) return <Navigate to={`/signin?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  if (session.kind === "staff") {
    location.replace("/classic/#/staff");
    return null;
  }
  return <AppShell />;
}

function Public({ children }: { children: React.ReactElement }) {
  const session = useSession((s) => s.session);
  return session?.kind === "customer" ? <Navigate to="/home" replace /> : children;
}

/** The staff console stays in the classic app. */
function ToClassic() {
  const loc = useLocation();
  location.replace(`/classic/#${loc.pathname}${loc.search}`);
  return null;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <HashRouter>
          <Routes>
            <Route path="/" element={<Public><Landing /></Public>} />
            <Route path="/signin" element={<Public><SignIn /></Public>} />
            <Route path="/register" element={<Public><Register /></Public>} />
            <Route path="/staff/*" element={<ToClassic />} />
            <Route element={<Customer />}>
              <Route path="/home" element={<Home />} />
              <Route path="/accounts" element={<Navigate to="/home" replace />} />
              <Route path="/accounts/:id" element={<AccountPage />} />
              <Route path="/pay" element={<Pay />} />
              <Route path="/receive" element={<Receive />} />
              <Route path="/activity" element={<Activity />} />
              <Route path="/inbox" element={<Inbox />} />
              <Route path="/deposits" element={<Deposits />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="/security" element={<Navigate to="/profile" replace />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <Prompts />
        </HashRouter>
        <Toaster position="top-center" richColors closeButton toastOptions={{ style: { fontFamily: "var(--font-sans)", borderRadius: 18 } }} />
      </MotionConfig>
    </QueryClientProvider>
  </React.StrictMode>,
);
