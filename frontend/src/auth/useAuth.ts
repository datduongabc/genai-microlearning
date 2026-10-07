import { createContext, useContext } from "react";
import type { UserInfo } from "../api";

// Lives apart from AuthProvider because Fast Refresh can only hot-swap a file
// that exports nothing but components; a hook next to one forces a full reload.

export interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: UserInfo | null;
}

export interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<UserInfo>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

// Where each role lands: the admin has no courses, and a lecturer has no admin page.
export function homeFor(role: string | undefined) {
  return role === "admin" ? "/admin" : "/courses";
}
