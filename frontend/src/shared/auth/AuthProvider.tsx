import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, loginRequest, logoutRequest, setTokens } from "../api/client";
import type { UserPublic } from "../api/types";

interface AuthState {
  user: UserPublic | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (...roles: UserPublic["role"][]) => boolean;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("pcn_access");
    if (!token) {
      setLoading(false);
      return;
    }
    api<UserPublic>("/auth/me")
      .then(setUser)
      .catch(() => {
        setTokens(null, null);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      login: async (email, password) => {
        const res = await loginRequest(email, password);
        setUser(res.user);
      },
      logout: async () => {
        await logoutRequest();
        setUser(null);
      },
      hasRole: (...roles) => (user ? roles.includes(user.role) : false),
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
