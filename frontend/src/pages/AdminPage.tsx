import { useState } from "react";
import { AdminFailuresPanel } from "../components/AdminFailuresPanel";
import { AdminUsersPanel } from "../components/AdminUsersPanel";
import { AdminStudiesPanel } from "../components/AdminStudiesPanel";
import { Tabs } from "../components/Tabs";
import { AppShell } from "../components/AppShell";

type Tab = "users" | "failures" | "studies";

const TABS: { id: Tab; label: string }[] = [
  { id: "users", label: "Tài khoản" },
  { id: "failures", label: "Job lỗi" },
  { id: "studies", label: "Nghiên cứu" },
];

export function AdminPage() {
  const [tab, setTab] = useState<Tab>("users");

  return (
    <AppShell title="Quản trị">
      <div className="admin-page">
      <Tabs tabs={TABS} selected={tab} onSelect={(id) => setTab(id as Tab)} label="Quản trị">
      {tab === "users" ? <AdminUsersPanel /> : tab === "failures" ? <AdminFailuresPanel /> : <AdminStudiesPanel />}
      </Tabs>
      </div>
    </AppShell>
  );
}
