import { useCallback, useEffect, useState } from "react";
import { getAdminCounts, type AdminCounts } from "../api";
import { AdminFailuresPanel } from "../components/AdminFailuresPanel";
import { AdminUsersPanel } from "../components/AdminUsersPanel";
import { AdminStudiesPanel } from "../components/AdminStudiesPanel";
import { Tabs } from "../components/Tabs";
import { AppShell } from "../components/AppShell";

type Tab = "users" | "failures" | "studies";

export function AdminPage() {
  const [tab, setTab] = useState<Tab>("users");
  const [counts, setCounts] = useState<AdminCounts | null>(null);
  // Bumped by a panel after a change, so the effect below is the only loader of the counts.
  const [countLoads, setCountLoads] = useState(0);
  const refreshCounts = useCallback(() => setCountLoads((n) => n + 1), []);
  // The counts only label the tabs and filters, so the page works without them.
  useEffect(() => {
    let cancelled = false;
    getAdminCounts().then((data) => { if (!cancelled) setCounts(data); }, () => {});
    return () => {
      cancelled = true;
    };
  }, [countLoads]);
  const tabs: { id: Tab; label: string }[] = [
    { id: "users", label: counts ? `Tài khoản (${counts.users})` : "Tài khoản" },
    { id: "failures", label: "Job lỗi" },
    { id: "studies", label: "Nghiên cứu" },
  ];

  return (
    <AppShell title="Quản trị">
      <div className="admin-page">
      <Tabs tabs={tabs} selected={tab} onSelect={(id) => setTab(id as Tab)} label="Quản trị">
      {tab === "users" ? <AdminUsersPanel counts={counts} onChange={refreshCounts} />
        : tab === "failures" ? <AdminFailuresPanel counts={counts} onChange={refreshCounts} /> : <AdminStudiesPanel />}
      </Tabs>
      </div>
    </AppShell>
  );
}
