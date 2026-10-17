import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { homeFor, useAuth } from "../auth/useAuth";
import { apiErrorMessage, getMoodleAccount, unlinkMoodleAccount, MOODLE_ACCOUNT_CHANGED_EVENT, type MoodleAccount } from "../api";
import { Brand } from "./AuthLayout";
import { MoodleAccountSection, type MoodleAccountState } from "./MoodleAccountSection";

function NavIcon({ kind }: { kind: "book" | "rating" | "admin" }) {
  return <svg className="shell-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === "book" ? <><path d="M4 4h6l2 2 2-2h6v16h-6l-2 2-2-2H4z" /><path d="M12 6v16" /></> : kind === "rating" ? <><path d="M9 4H5v17h14V4h-4M9 3h6v4H9z" /><path d="m8 14 3 3 5-6" /></> : <><path d="m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7z" /><path d="m8 12 3 3 5-6" /></>}
  </svg>;
}

export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [account, setAccount] = useState<MoodleAccount | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    if (!user?.is_admin) getMoodleAccount().then(result => { if (!cancelled) setAccount(result); }, () => { if (!cancelled) setError("Không kiểm tra được tài khoản Moodle."); });
    const outside = (event: PointerEvent) => { if (!container.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => { cancelled = true; document.removeEventListener("pointerdown", outside); };
  }, [open, user?.is_admin]);

  const linked = useCallback((status: MoodleAccountState) => {
    if (status !== "linked") return;
    setAccount(null);
    getMoodleAccount().then(setAccount, () => setError("Không kiểm tra được tài khoản Moodle."));
  }, []);

  function close() { setOpen(false); trigger.current?.focus(); }
  async function unlink() {
    if (!window.confirm("Hủy liên kết Moodle? Bạn sẽ không xuất bản lên Moodle được cho đến khi liên kết lại.")) return;
    setBusy(true); setError(null);
    try {
      await unlinkMoodleAccount();
      setAccount({ status: "not_linked", email: "" });
      window.dispatchEvent(new Event(MOODLE_ACCOUNT_CHANGED_EVENT));
    } catch (err) { setError(apiErrorMessage(err, "Không hủy được liên kết Moodle.")); }
    finally { setBusy(false); }
  }

  return <div className="min-h-screen bg-cream">
    <a className="sr-only focus:not-sr-only" href="#main-content">Đến nội dung chính</a>
    <aside className="sidebar" aria-label="Thanh bên">
      <Link to={homeFor(user?.is_admin)}><Brand /></Link>
      <nav className="shell-nav" aria-label="Điều hướng chính">
        {!user?.is_admin && <NavLink to="/courses"><NavIcon kind="book" />Môn học</NavLink>}
        <NavLink to="/studies"><NavIcon kind="rating" />Chấm nghiên cứu</NavLink>
        {user?.is_admin && <NavLink to="/admin"><NavIcon kind="admin" />Quản trị</NavLink>}
      </nav>
      <div className="account" ref={container} onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); close(); }
        if (open && !(event.target as HTMLElement).matches("input, textarea, select") && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          const items = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
          if (!items.length) return;
          event.preventDefault();
          const index = items.indexOf(document.activeElement as HTMLButtonElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
          items[next].focus();
        }
      }}>
        {open && <div className="account-menu" id="account-menu" ref={panel} role="region" aria-label="Tài khoản">
          {!user?.is_admin && (account?.status === "linked" ? <>
            <div className="account-info">Moodle đã liên kết<div className="text-gray-600">{account.email}</div></div>
            <button className="danger" disabled={busy} onClick={() => void unlink()}>{busy ? "Đang hủy liên kết…" : "Hủy liên kết Moodle…"}</button>
          </> : account && <MoodleAccountSection onStatus={linked} />)}
          {error && <p role="alert" className="account-info text-red-700">{error}</p>}
          <button onClick={logout} data-leaves-page>Đăng xuất</button>
        </div>}
        <button className="account-trigger" ref={trigger} aria-expanded={open} aria-controls="account-menu" onClick={() => { setError(null); setAccount(null); setOpen(!open); }}>
          <span className="avatar" aria-hidden="true">{user?.name.split(/\s+/).map(word => word[0]).slice(-2).join("")}</span>
          <span className="min-w-0 flex-1"><span className="block truncate font-semibold">{user?.name}</span><span className="brand-sub">{user?.is_admin ? "Quản trị viên" : "Giảng viên"}</span></span>
          <span aria-hidden="true">⌃</span>
        </button>
      </div>
    </aside>
    <main id="main-content" className="shell-main" tabIndex={-1}><div className="shell-content"><h1 className="page-title">{title}</h1>{children}</div></main>
  </div>;
}
