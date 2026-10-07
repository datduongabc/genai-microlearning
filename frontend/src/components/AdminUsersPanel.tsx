import { useCallback, useEffect, useState } from "react";
import {
  apiErrorMessage,
  deleteUser,
  listUsers,
  setUserActive,
  type AdminUser,
  type MoodleStatus,
} from "../api";
import { useAuth } from "../auth/useAuth";

const MOODLE_LABEL: Record<MoodleStatus, { text: string; color: string }> = {
  linked: { text: "Đã liên kết Moodle", color: "text-green-700" },
  not_linked: { text: "Chưa liên kết Moodle", color: "text-amber-700" },
};

// Why a lecturer's account cannot be deleted, or null when it was never used: courses and the
// Moodle courses they linked or created stay theirs, so such an account can only be locked.
function deleteBlocker(u: AdminUser): string | null {
  const reasons = [
    (u.course_count ?? 0) > 0 && `${u.course_count} môn học`,
    (u.moodle_course_count ?? 0) > 0 && `${u.moodle_course_count} môn học Moodle đã liên kết hoặc tạo`,
  ].filter(Boolean);
  return reasons.length > 0 ? reasons.join(", ") : null;
}

// What the admin does with lecturers' accounts: lock and unlock, and delete an account that was never
// used. Lecturers come from Moodle (their account is made at their first Moodle login, with the
// Moodle link shown here) and their passwords live there, so there is nothing to create or reset. A
// lock never deletes anything; a lecturer with courses or Moodle courses can only be locked, so
// courses, modules and experiment data are never lost.
export function AdminUsersPanel() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Bumped to load the list again (after a change), so the effect below is the only loader.
  const [reloads, setReloads] = useState(0);
  const reload = useCallback(() => setReloads((count) => count + 1), []);

  // Sets state only from promise callbacks, and drops an answer that arrives after a newer request.
  useEffect(() => {
    let cancelled = false;
    listUsers().then(
      (list) => {
        if (cancelled) return;
        setUsers(list);
        setLoadError(null);
      },
      (err) => {
        if (!cancelled) setLoadError(apiErrorMessage(err, "Không tải được danh sách tài khoản."));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [reloads]);

  async function handleToggleActive(user: AdminUser) {
    setBusyId(user.id);
    setError(null);
    setNotice(null);
    try {
      await setUserActive(user.id, !user.is_active);
      setNotice(user.is_active ? `Đã khóa tài khoản ${user.email}.` : `Đã mở khóa tài khoản ${user.email}.`);
      reload();
    } catch (err) {
      setError(apiErrorMessage(err, "Đổi trạng thái tài khoản thất bại"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(user: AdminUser) {
    const confirmed = window.confirm(
      `Xóa vĩnh viễn tài khoản "${user.name}" (${user.email})? Tài khoản chưa có môn học nào, kể cả trên Moodle. Các phiên đăng nhập của họ bị đóng và không thể khôi phục. Tài khoản Moodle liên kết (nếu có) sẽ KHÔNG bị xóa.`,
    );
    if (!confirmed) return;

    setBusyId(user.id);
    setError(null);
    setNotice(null);
    try {
      await deleteUser(user.id);
      setNotice(`Đã xóa tài khoản ${user.email}.`);
      reload();
    } catch (err) {
      setError(apiErrorMessage(err, "Xóa tài khoản thất bại"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      {notice && <p className="text-sm text-green-800">{notice}</p>}
      {error && <p className="text-sm text-red-700">{error}</p>}
      {loadError && <p className="text-sm text-red-700">{loadError}</p>}

      {users === null ? (
        !loadError && <p className="text-sm text-gray-500">Đang tải...</p>
      ) : (
        <ul className="flex flex-col divide-y divide-gray-200">
          {users.map((u) => {
            const blocker = u.role === "teacher" ? deleteBlocker(u) : null;
            return (
            <li key={u.id} className="py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium">
                    {u.name}
                    {u.role === "admin" && (
                      <span className="ml-2 rounded-[3px] bg-navy px-1.5 py-0.5 text-[11px] font-semibold text-[#f3f1ea]">
                        Admin
                      </span>
                    )}
                  </div>
                  <div className="break-all text-sm text-gray-500">{u.email}</div>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs font-medium">
                    <span className={u.is_active ? "text-green-700" : "text-red-700"}>
                      {u.is_active ? "Đang hoạt động" : "Đã khóa"}
                    </span>
                    {blocker && (
                      <span className="text-gray-500">
                        {blocker} (chỉ có thể khóa, không xóa được)
                      </span>
                    )}
                    {u.moodle_status && (
                      <span className={MOODLE_LABEL[u.moodle_status].color}>
                        {MOODLE_LABEL[u.moodle_status].text}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-4 text-sm">
                  <button
                    onClick={() => handleToggleActive(u)}
                    disabled={busyId === u.id || u.id === me?.id}
                    title={u.id === me?.id ? "Không thể tự khóa tài khoản của mình" : undefined}
                    className={`hover:underline disabled:cursor-not-allowed disabled:opacity-50 ${
                      u.is_active ? "text-red-700" : "font-semibold text-green-700"
                    }`}
                  >
                    {u.is_active ? "Khóa" : "Mở khóa"}
                  </button>
                  {u.role === "teacher" && (
                    <button
                      onClick={() => handleDelete(u)}
                      disabled={busyId === u.id || blocker !== null}
                      title={
                        blocker
                          ? `Giảng viên đang có ${blocker} nên không thể xóa, chỉ có thể khóa tài khoản.`
                          : undefined
                      }
                      className="text-red-700 hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Xóa
                    </button>
                  )}
                </div>
              </div>
            </li>
            );
          })}
          {users.length === 0 && <p className="py-4 text-sm text-gray-500">Chưa có tài khoản nào: giảng viên xuất hiện ở đây sau lần đăng nhập đầu tiên bằng tài khoản Moodle.</p>}
        </ul>
      )}
    </div>
  );
}
