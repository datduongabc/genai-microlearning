import { useState } from "react";
import { AdminFailuresPanel } from "../components/AdminFailuresPanel";
import { AdminUsersPanel } from "../components/AdminUsersPanel";
import { AppShell } from "../components/AppShell";

type Tab = "users" | "failures";

const TABS: { id: Tab; label: string }[] = [
  { id: "users", label: "Giảng viên" },
  { id: "failures", label: "Theo dõi lỗi" },
];

export function AdminPage() {
  const [tab, setTab] = useState<Tab>("users");

  return (
    <AppShell title="Quản trị">
      <div role="tablist" className="mb-6 flex gap-6 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 pb-2 text-sm font-semibold ${
              tab === t.id ? "border-terracotta text-terracotta" : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "users" ? <AdminUsersPanel /> : <AdminFailuresPanel />}
    </AppShell>
  );
}
