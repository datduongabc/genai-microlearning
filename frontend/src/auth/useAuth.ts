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
  login: (email: string, password: string) => Promise<UserInfo>;
  /** Signs in with the code of an LTI launch and returns the course it opened, and whether it is the user's own
   * (a member's launch opens the course chat only). */
  loginWithLaunchCode: (code: string) => Promise<{ courseId: string; owner: boolean }>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function homeFor(isAdmin: boolean | undefined) {
  return isAdmin ? "/admin" : "/courses";
}
