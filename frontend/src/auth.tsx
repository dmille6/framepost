import { createContext, useContext, useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, fetchMe, login as apiLogin, logout as apiLogout, type Me } from "./api/client";

type AuthState = {
  user: Me | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await fetchMe();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
  });

  const loginMutation = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      apiLogin(username, password),
    onSuccess: (user) => qc.setQueryData(["me"], user),
  });

  const logoutMutation = useMutation({
    mutationFn: apiLogout,
    onSuccess: () => qc.setQueryData(["me"], null),
  });

  const value: AuthState = {
    user: meQuery.data ?? null,
    isLoading: meQuery.isLoading,
    login: async (username, password) => loginMutation.mutateAsync({ username, password }),
    logout: async () => { await logoutMutation.mutateAsync(); },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const location = useLocation();
  // Bootstrap the CSRF cookie before any unauth'd nav.
  useEffect(() => { void fetch("/health").catch(() => {}); }, []);
  if (isLoading) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <span className="fp-spinner" style={{ width: 22, height: 22 }} />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}
