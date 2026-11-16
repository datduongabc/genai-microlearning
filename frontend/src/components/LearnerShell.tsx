import type { ReactNode } from "react";
import { useAuth } from "../auth/useAuth";
import { Brand } from "./AuthLayout";

// The frame of every page a student sees: brand, who they are, and a way out.
export function LearnerShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="learner-page">
      <header className="learner-header">
        <Brand />
        <div className="learner-user">
          {user && <span className="muted">{user.name}</span>}
          <button type="button" onClick={logout} className="ui-button">
            Đăng xuất
          </button>
        </div>
      </header>
      <main className="learner-main">{children}</main>
    </div>
  );
}
