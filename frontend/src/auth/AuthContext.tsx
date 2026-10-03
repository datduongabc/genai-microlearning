import { useEffect, useState, type ReactNode } from "react";
import * as authApi from "../api";
import { AuthContext, type AuthState } from "./useAuth";

function readStoredAuth(): AuthState {
  const raw = localStorage.getItem("auth");
  if (!raw) return { token: null, refreshToken: null, user: null };
  try {
    return JSON.parse(raw) as AuthState;
  } catch {
    return { token: null, refreshToken: null, user: null };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(readStoredAuth);

  useEffect(() => {
    if (state.token) {
      localStorage.setItem("auth", JSON.stringify(state));
    } else {
      localStorage.removeItem("auth");
    }
  }, [state]);

  async function login(email: string, password: string) {
    const data = await authApi.login(email, password);
    setState({ token: data.access_token, refreshToken: data.refresh_token, user: data.user });
    return data.user;
  }

  function logout() {
    // access token giờ chỉ sống 1h nên api.ts tự refresh ngầm khi cần --
    // logout() phải chủ động thu hồi refresh_token ở server (không chỉ xóa
    // local), nếu không token đó vẫn dùng được tới khi tự hết hạn sau 7 ngày.
    if (state.refreshToken) void authApi.logout(state.refreshToken);
    setState({ token: null, refreshToken: null, user: null });
  }

  return (
    <AuthContext.Provider value={{ ...state, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
