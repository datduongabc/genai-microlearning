import type { ReactNode } from "react";

export function Brand() {
  return <div className="brand"><span className="brand-logo" aria-hidden="true">G</span><div><div className="brand-name">GENAILMS</div><div className="brand-sub">Microlearning từ tài liệu</div></div></div>;
}

export function AuthLayout({ children }: { children: ReactNode }) {
  return <div className="auth-layout">
    <aside className="auth-brand">
      <Brand />
      <div className="auth-pitch">
        <h2>Biến tài liệu môn học thành bài học ngắn, có trích dẫn nguồn.</h2>
        <div className="auth-features">
          <div className="auth-feature"><span aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z" /></svg></span>Sinh bài học 7 phần từ tài liệu của bạn</div>
          <div className="auth-feature"><span aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="M3 5h7v7l-4 7H3l3-7H3zm11 0h7v7l-4 7h-3l3-7h-3z" /></svg></span>Bấm số trích dẫn để xem đoạn gốc</div>
          <div className="auth-feature"><span aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round"><path d="m22 2-7 20-4-9-9-4zM22 2 11 13" /></svg></span>Duyệt, sửa, rồi đưa lên Moodle</div>
        </div>
      </div>
      <span />
    </aside>
    <main className="auth-area">{children}</main>
  </div>;
}
