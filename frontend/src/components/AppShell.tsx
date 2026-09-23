import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";

export function AppShell({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-cream">
      <header className="flex items-center justify-between border-b border-gray-200 px-8 py-4">
        <Link to="/courses" className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-navy p-1">
            <img
              src="/logo.png"
              className="h-full w-full object-contain"
              alt=""
            />
          </div>
          <span className="font-serif font-semibold">HELLO LMS</span>
        </Link>
        <div className="flex items-center gap-4 text-sm text-gray-500">
          <Link to="/monitoring" className="hover:text-terracotta">
            Theo dõi lỗi
          </Link>
          <span>{user?.name}</span>
          <button onClick={logout} className="hover:text-terracotta">
            Đăng xuất
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-8 py-10">
        <h1 className="mb-8 font-serif text-2xl font-semibold">{title}</h1>
        {children}
      </main>
    </div>
  );
}
