import { useDocumentTitle } from "../lib/documentTitle";
import { notifyDone, notifyFailed } from "../lib/notify";
import { confirmAction } from "../lib/confirm";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { homeFor, useAuth } from "../auth/useAuth";
import { apiErrorMessage, getMoodleAccount, unlinkMoodleAccount, MOODLE_ACCOUNT_CHANGED_EVENT, type MoodleAccount } from "../api";
import { Brand } from "./AuthLayout";
import { MoodleAccountSection, type MoodleAccountState } from "./MoodleAccountSection";
import { initials } from "../lib/initials";
import { confirmLeavingUnsavedWork } from "../lib/useLeaveGuard";
import { useMediaQuery } from "../lib/useMediaQuery";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "./ui/sheet";
import { BookOpen, ChevronDown, ClipboardCheck, Link2, LogOut, Menu, PanelLeftClose, PanelLeftOpen, ShieldCheck, Unlink } from "lucide-react";

const NAV_ICON = { book: BookOpen, rating: ClipboardCheck, admin: ShieldCheck };
function NavIcon({ kind }: { kind: keyof typeof NAV_ICON }) {
  const Icon = NAV_ICON[kind];
  return <Icon className="shell-icon" strokeWidth={1.75} aria-hidden="true" />;
}

const ACCOUNT_ICON = { link: Link2, unlink: Unlink, logout: LogOut };
function AccountIcon({ kind }: { kind: keyof typeof ACCOUNT_ICON }) {
  const Icon = ACCOUNT_ICON[kind];
  return <Icon className="shell-icon account-icon" strokeWidth={1.75} aria-hidden="true" />;
}

// Remembered in this browser only: a layout preference, not worth a request. Storage that is
// blocked or full leaves the sidebar working, just not remembered.
const COLLAPSED_KEY = "sidebar-collapsed";
function readCollapsed() {
  try { return window.localStorage.getItem(COLLAPSED_KEY) === "1"; } catch { return false; }
}

// `fullTitle` is for a title that was cut short: the whole text stays available on hover.
// `actions` sits beside the page title, e.g. a "⋯" menu with what can be done to the whole page.
export function AppShell({ title, fullTitle, actions, children }: {
  title: string; fullTitle?: string; actions?: ReactNode; children: ReactNode;
}) {
  useDocumentTitle(fullTitle ?? title);
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const mobile = useMediaQuery("(max-width: 700px)");
  const [drawer, setDrawer] = useState(false);
  function toggleCollapsed() {
    setCollapsed((was) => {
      try { window.localStorage.setItem(COLLAPSED_KEY, was ? "0" : "1"); } catch { /* not remembered */ }
      return !was;
    });
  }
  const [account, setAccount] = useState<MoodleAccount | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    if (!user?.is_admin) getMoodleAccount().then(result => { if (!cancelled) setAccount(result); }, () => { if (!cancelled) setError("Không kiểm tra được tài khoản Moodle."); });
    const outside = (event: PointerEvent) => { if (!container.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => { cancelled = true; document.removeEventListener("pointerdown", outside); };
  }, [open, user?.is_admin]);

  const linked = useCallback((status: MoodleAccountState) => {
    if (status !== "linked") return;
    if (document.activeElement === document.body || panel.current?.contains(document.activeElement)) panel.current?.querySelector<HTMLButtonElement>("[data-leaves-page]")?.focus();
    setAccount(null);
    getMoodleAccount().then(setAccount, () => setError("Không kiểm tra được tài khoản Moodle."));
  }, []);

  function close() { setOpen(false); trigger.current?.focus(); }
  async function unlink() {
    if (!await confirmAction({ title: "Hủy liên kết Moodle?", description: "Bạn sẽ không xuất bản lên Moodle được cho đến khi liên kết lại.", action: "Hủy liên kết" })) return;
    setBusy(true);
    try {
      await unlinkMoodleAccount();
      setAccount({ status: "not_linked", email: "" });
      panel.current?.querySelector<HTMLButtonElement>("[data-leaves-page]")?.focus();
      window.dispatchEvent(new Event(MOODLE_ACCOUNT_CHANGED_EVENT));
      notifyDone("Đã hủy liên kết Moodle.");
    } catch (err) { notifyFailed(apiErrorMessage(err, "Không hủy được liên kết Moodle.")); }
    finally { setBusy(false); }
  }

  // One copy of the navigation: a fixed sidebar on wide screens, a drawer behind a menu button on phones.
  const sidebarBody = <>
      <div className="sidebar-head">
        <Link to={homeFor(user?.is_admin)} aria-label="GENAILMS, trang chủ"><Brand /></Link>
        <button type="button" className="sidebar-toggle" onClick={toggleCollapsed} aria-expanded={!collapsed}
          aria-label={collapsed ? "Mở rộng thanh bên" : "Thu gọn thanh bên"} data-tip={collapsed ? "Mở rộng thanh bên" : undefined}>
          {collapsed ? <PanelLeftOpen className="shell-icon" strokeWidth={1.75} aria-hidden="true" /> : <PanelLeftClose className="shell-icon" strokeWidth={1.75} aria-hidden="true" />}
        </button>
      </div>
      <nav className="shell-nav" aria-label="Điều hướng chính">
        <NavLink to="/courses" data-tip="Môn học"><NavIcon kind="book" /><span className="shell-label">Môn học</span></NavLink>
        <NavLink to="/studies" data-tip="Nghiên cứu"><NavIcon kind="rating" /><span className="shell-label">Nghiên cứu</span></NavLink>
        {user?.is_admin && <NavLink to="/admin" data-tip="Quản trị"><NavIcon kind="admin" /><span className="shell-label">Quản trị</span></NavLink>}
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
          <div className="account-info"><strong>{user?.name}</strong><div className="text-gray-600">{user?.email}</div></div>
          <hr />
          {!user?.is_admin && (account?.status === "linked" ? <>
            <div className="account-info"><AccountIcon kind="link" />Moodle đã liên kết<div className="text-gray-600">{account.email}</div></div>
            <button className="menu-item danger" disabled={busy} onClick={() => void unlink()}><AccountIcon kind="unlink" />{busy ? "Đang hủy liên kết…" : "Hủy liên kết Moodle…"}</button>
          </> : account && <MoodleAccountSection onStatus={linked} />)}
          {error && <p role="alert" className="account-info danger-text">{error}</p>}
          {(!user?.is_admin && account || error) && <hr />}
          <button className="menu-item" onClick={() => { if (confirmLeavingUnsavedWork()) logout(); }} data-leaves-page><AccountIcon kind="logout" />Đăng xuất</button>
        </div>}
        <button className="account-trigger" data-tip={user?.name} ref={trigger} aria-label={`Tài khoản ${user?.name}, ${user?.is_admin ? "Quản trị viên" : "Giảng viên"}`} aria-expanded={open} aria-controls="account-menu" onClick={() => { setError(null); setAccount(null); setOpen(!open); }}>
          <span className="avatar" aria-hidden="true">{user && initials(user.name)}</span>
          <span className="shell-label min-w-0 flex-1"><span className="block truncate font-semibold">{user?.name}</span><span className="brand-sub">{user?.is_admin ? "Quản trị viên" : "Giảng viên"}</span></span>
          <ChevronDown className="shell-icon account-chevron" aria-hidden="true" />
        </button>
      </div>
  </>;

  return <div className="min-h-screen bg-paper" data-sidebar={collapsed ? "collapsed" : "expanded"}>
    <a className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2" href="#main-content">Đến nội dung chính</a>
    {mobile ? <header className="mobile-bar">
      <Link to={homeFor(user?.is_admin)} aria-label="GENAILMS, trang chủ"><Brand /></Link>
      <Sheet open={drawer} onOpenChange={setDrawer}>
        <SheetTrigger className="mobile-menu-button" aria-label="Mở menu"><Menu className="shell-icon" aria-hidden="true" /></SheetTrigger>
        <SheetContent side="left" className="sidebar-sheet" aria-describedby={undefined}
          onOpenAutoFocus={(event) => { event.preventDefault(); (event.currentTarget as HTMLElement).querySelector<HTMLElement>(".shell-nav a[aria-current], .shell-nav a")?.focus(); }} onClick={(event) => { if ((event.target as HTMLElement).closest(".shell-nav a")) setDrawer(false); }}>
          <SheetTitle className="sr-only">Menu</SheetTitle>
          {sidebarBody}
        </SheetContent>
      </Sheet>
    </header> : <aside className="sidebar" aria-label="Thanh bên">{sidebarBody}</aside>}
    <main id="main-content" className="shell-main" tabIndex={-1}><div className="shell-content">{actions
      ? <div className="page-head"><h1 className="page-title" title={fullTitle}>{title}</h1>{actions}</div>
      : <h1 className="page-title" title={fullTitle}>{title}</h1>}{children}</div></main>
  </div>;
}
