import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loading } from "../components/ui";
import { api, refreshSession, session } from "./api";
import type { Me, Tokens } from "./types";

type Status = "checking" | "signed-in" | "signed-out";

interface AuthValue {
  status: Status;
  me: Me | null;
  expired: boolean;
  /** Set when the session could not be checked because of a network failure, not because it ended. */
  checkFailed: boolean;
  recheck: () => void;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>(session.hasRefreshToken() ? "checking" : "signed-out");
  const [me, setMe] = useState<Me | null>(null);
  const [expired, setExpired] = useState(false);
  const [checkFailed, setCheckFailed] = useState(false);

  const endSession = useCallback(
    (wasExpired: boolean) => {
      session.clear();
      // Nothing from this user may be visible to whoever signs in next.
      queryClient.clear();
      setMe(null);
      setExpired(wasExpired);
      setStatus("signed-out");
    },
    [queryClient],
  );

  useEffect(() => {
    session.onExpired(() => endSession(true));
  }, [endSession]);

  const restore = useCallback(async () => {
    setStatus("checking");
    setCheckFailed(false);
    try {
      if (!(await refreshSession())) return endSession(true);
      setMe(await api<Me>("/auth/me"));
      setStatus("signed-in");
    } catch {
      // Keep the refresh token: the server may just be unreachable right now.
      setCheckFailed(true);
    }
  }, [endSession]);

  useEffect(() => {
    if (session.hasRefreshToken()) void restore();
  }, [restore]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await api<Tokens>("/auth/login", { method: "POST", json: { email, password }, authenticated: false });
      session.store(tokens);
      queryClient.clear();
      setMe(await api<Me>("/auth/me"));
      setExpired(false);
      setStatus("signed-in");
    },
    [queryClient],
  );

  const register = useCallback(
    async (name: string, email: string, password: string) => {
      await api<Me>("/auth/register", { method: "POST", json: { name, email, password }, authenticated: false });
      await login(email, password);
    },
    [login],
  );

  const logout = useCallback(() => endSession(false), [endSession]);

  const value = useMemo<AuthValue>(() => ({ status, me, expired, checkFailed, recheck: () => void restore(), login, register, logout }), [status, me, expired, checkFailed, restore, login, register, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Only same-site paths, so a crafted login link can't send someone to another website. */
export function safeNextPath(next: string | null) {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.includes("\\") ? next : "/";
}

export function RequireAuth() {
  const { status, checkFailed, recheck } = useAuth();
  const location = useLocation();
  if (checkFailed) {
    return (
      <div className="mx-auto mt-16 max-w-md space-y-3 rounded-lg border border-slate-200 bg-white p-6 text-center text-sm shadow-sm">
        <p role="alert">We couldn't reach the server. Check your connection and try again.</p>
        <button type="button" onClick={recheck} className="font-medium text-brand-600 hover:underline">
          Try again
        </button>
      </div>
    );
  }
  if (status === "checking") return <Loading label="Checking your session…" />;
  if (status === "signed-out") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <Outlet />;
}
