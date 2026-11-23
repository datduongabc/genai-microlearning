import { useEffect, useState, type ReactNode } from "react";
import * as authApi from "../api";
import { storedAuth } from "../api/client";
import { AuthContext, type AuthState } from "./useAuth";

function readStoredAuth(): AuthState {
  return (storedAuth() as AuthState | null) ?? { token: null, refreshToken: null, user: null };
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

  async function loginWithLaunchCode(code: string) {
    const data = await authApi.ltiSession(code);
    setState({ token: data.access_token, refreshToken: data.refresh_token, user: data.user });
    return { courseId: data.course_id, owner: data.course_role !== "member" };
  }

  function logout() {
    // access token chỉ sống 15 phút nên api.ts tự refresh ngầm khi cần --
    // logout() phải chủ động thu hồi refresh_token ở server (không chỉ xóa
    // local), nếu không token đó vẫn dùng được tới khi tự hết hạn sau 14 ngày.
    // Đọc từ localStorage, không từ state: client.ts ghi token mới vào đó mỗi lần
    // refresh, còn token trong state là token lúc login, server đã thu hồi rồi.
    const { refreshToken } = readStoredAuth();
    if (refreshToken) void authApi.logout(refreshToken);
    setState({ token: null, refreshToken: null, user: null });
  }

  return (
    <AuthContext.Provider value={{ ...state, login, loginWithLaunchCode, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
