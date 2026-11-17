import type { ReactNode } from "react";

export function Brand() {
  return <div className="brand"><span className="brand-logo" aria-hidden="true">G</span><div><div className="brand-name">GENAILMS</div><div className="brand-sub">Microlearning từ tài liệu</div></div></div>;
}

export function AuthLayout({ children }: { children: ReactNode }) {
  return <main className="auth-area">
    <div className="auth-column">
      <Brand />
      {children}
    </div>
  </main>;
}
