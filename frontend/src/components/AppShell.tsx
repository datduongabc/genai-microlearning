import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { homeFor, useAuth } from "../auth/useAuth";

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
      {/* Lề 16px trên điện thoại và không cho chữ gãy dòng: ở 390px, lề 32px cộng đủ ba mục
          điều hướng là khoảng 440px, nên "HELLO LMS" và từng mục đều bị gãy làm hai dòng. */}
      <header className="flex items-center justify-between gap-4 border-b border-gray-200 px-4 py-4 sm:px-8">
        <Link to={homeFor(user?.is_admin)} className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-navy p-1">
            <img
              src="/logo.png"
              className="h-full w-full object-contain"
              alt=""
            />
          </div>
          <span className="whitespace-nowrap font-serif font-semibold">HELLO LMS</span>
        </Link>
        <div className="flex items-center gap-3 whitespace-nowrap text-sm text-gray-500 sm:gap-4">
          {/* Tên chỉ mang tính thông tin nên nhường chỗ trên màn hình nhỏ. */}
          <span className="hidden sm:inline">{user?.name}</span>
          <button onClick={logout} className="hover:text-terracotta">
            Đăng xuất
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-10 sm:px-8">
        <h1 className="mb-8 font-serif text-2xl font-semibold">{title}</h1>
        {children}
      </main>
    </div>
  );
}
