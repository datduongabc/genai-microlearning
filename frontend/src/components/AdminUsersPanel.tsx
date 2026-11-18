import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  apiErrorMessage,
  createUser,
  listUsers,
  resetUserPassword,
  setUserActive,
  unlinkUserMoodleAccount,
  type AdminCounts,
  type AdminUser,
} from "../api";
import {
  createUserSchema,
  resetPasswordSchema,
  type CreateUserFormValues,
} from "../lib/authSchemas";
import { initials } from "../lib/initials";

// Account activation preserves existing learning data and Moodle links.
export function AdminUsersPanel({ counts, onChange }: { counts: AdminCounts | null; onChange: () => void }) {
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("active");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);

  // Bumped to load the list again (after a change), so the effect below is the only loader.
  const [reloads, setReloads] = useState(0);
  const reload = useCallback(() => {
    setReloads((count) => count + 1);
    onChange();
  }, [onChange]);

  // One row menu at a time; a press anywhere outside the open one closes it.
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => {
      if (!(event.target instanceof Element && event.target.closest(".user-actions"))) setMenu(null);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menu]);

  // Sets state only from promise callbacks, and drops an answer that arrives after a newer request.
  useEffect(() => {
    let cancelled = false;
    listUsers(filter === "active").then(
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
  }, [reloads, filter]);

  async function handleToggleActive(user: AdminUser) {
    if (!window.confirm(`${user.is_active ? "Vô hiệu hóa" : "Kích hoạt lại"} tài khoản ${user.email}? Dữ liệu và liên kết Moodle được giữ nguyên.`)) return;
    setBusyId(user.id);
    setError(null);
    setNotice(null);
    try {
      await setUserActive(user.id, !user.is_active);
      setNotice(user.is_active ? `Đã vô hiệu hóa tài khoản ${user.email}.` : `Đã kích hoạt lại tài khoản ${user.email}.`);
      reload();
    } catch (err) {
      setError(apiErrorMessage(err, "Đổi trạng thái tài khoản thất bại"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleUnlinkMoodle(user: AdminUser) {
    if (!window.confirm(`Hủy liên kết tài khoản Moodle của ${user.email}? Các khóa học và nội dung trên Moodle được giữ nguyên. Người dùng cần liên kết lại tài khoản để tiếp tục đẩy nội dung lên Moodle.`)) return;
    setBusyId(user.id);
    setError(null);
    setNotice(null);
    try {
      await unlinkUserMoodleAccount(user.id);
      setNotice(`Đã hủy liên kết Moodle của ${user.email}.`);
      reload();
    } catch (err) {
      setError(apiErrorMessage(err, "Không hủy được liên kết Moodle."));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="admin-users">
      <div className="admin-toolbar">
        <div role="group" aria-label="Trạng thái tài khoản" className="segmented">
          <button aria-pressed={filter === "active"} onClick={() => setFilter("active")}>Đang hoạt động{counts && ` (${counts.users - counts.inactive_users})`}</button>
          <button aria-pressed={filter === "inactive"} onClick={() => setFilter("inactive")}>Đã vô hiệu hóa{counts && ` (${counts.inactive_users})`}</button>
        </div>
        <button className="ui-button primary" aria-expanded={creating} onClick={() => setCreating(!creating)}>{creating ? "Đóng" : "+ Tạo tài khoản"}</button>
      </div>
      {creating && <CreateUserForm
        onCreated={(created) => {
          setCreating(false);
          setError(null);
          setNotice(`Đã tạo tài khoản cho ${created.email}.`);
          reload();
        }}
      />}

      {notice && <p className="text-sm text-green-800">{notice}</p>}
      {error && <p className="text-sm text-red-700">{error}</p>}
      {loadError && <p className="text-sm text-red-700">{loadError}</p>}

      {users === null ? (
        !loadError && <p className="text-sm text-gray-500">Đang tải...</p>
      ) : (
        <ul className="account-table ui-card">
          <li className="account-row table-heading"><span>Người dùng</span><span>Moodle</span><span /></li>
          {users.map((u) => (
            <li key={u.id}>
              <div className="account-row">
                <div className="flex min-w-0 items-center gap-3">
                  <span className={`avatar${u.is_admin ? " admin" : ""}`} aria-hidden="true">{initials(u.name)}</span>
                  <div className="min-w-0"><div className="flex flex-wrap items-center gap-2 font-semibold">{u.name}
                    {u.is_admin && <span className="admin-badge">Quản trị viên</span>}
                  </div><div className="small muted break-all">{u.email}</div></div>
                </div>
                <span className={`small ${u.moodle_status === "linked" ? "success-text" : "muted"}`}>
                  {u.moodle_status === null ? "—" : u.moodle_status === "linked" ? "↗ Đã liên kết" : "Chưa liên kết"}
                </span>
                <div className="user-actions" onKeyDown={(e) => {
                  if (e.key !== "Escape" || menu !== u.id) return;
                  setMenu(null);
                  e.currentTarget.querySelector("button")?.focus();
                }}>
                  <button aria-label={`Thao tác ${u.email}`} aria-expanded={menu === u.id}
                    onClick={() => setMenu(menu === u.id ? null : u.id)}>⋯</button>
                  {menu === u.id && <div className="user-actions-menu" onClick={(e) => {
                    setMenu(null);
                    e.currentTarget.parentElement?.querySelector("button")?.focus();
                  }}>
                    <button onClick={() => setResetting(resetting === u.id ? null : u.id)}>Đặt lại mật khẩu</button>
                    {u.moodle_status === "linked" && <button disabled={busyId === u.id} onClick={() => void handleUnlinkMoodle(u)}>Hủy liên kết Moodle</button>}
                    <button disabled={busyId === u.id || u.is_admin} onClick={() => void handleToggleActive(u)}
                      title={u.is_admin ? "Không thể vô hiệu hóa tài khoản admin" : undefined} className="danger-text">
                      {u.is_active ? "Vô hiệu hóa" : "Kích hoạt lại"}
                    </button>
                  </div>}
                </div>
              </div>
              {resetting === u.id && (
                <ResetPasswordForm
                  user={u}
                  onDone={(message) => {
                    setResetting(null);
                    setError(null);
                    setNotice(message);
                  }}
                />
              )}
            </li>
          ))}
          {users.length === 0 && <li className="account-empty">Chưa có tài khoản nào.</li>}
        </ul>
      )}
    </div>
  );
}

function CreateUserForm({ onCreated }: { onCreated: (user: AdminUser) => void }) {
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CreateUserFormValues>({ resolver: zodResolver(createUserSchema) });

  async function onSubmit(values: CreateUserFormValues) {
    setError(null);
    try {
      const created = await createUser(values.name, values.email, values.password);
      reset();
      onCreated(created);
    } catch (err) {
      setError(apiErrorMessage(err, "Tạo tài khoản thất bại"));
    }
  }

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      className="ui-card flex flex-col gap-4 p-6"
    >
      <h2 className="text-h2 font-semibold">Tạo tài khoản người dùng</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-sm text-gray-500">
          Họ và tên
          <input type="text" autoComplete="off" className="auth-input" {...register("name")} />
          {errors.name && <span className="text-red-600">{errors.name.message}</span>}
        </label>
        <label className="flex flex-col gap-1 text-sm text-gray-500">
          Email
          <input type="email" autoComplete="off" className="auth-input" {...register("email")} />
          {errors.email && <span className="text-red-600">{errors.email.message}</span>}
        </label>
        <label className="flex flex-col gap-1 text-sm text-gray-500">
          Mật khẩu ban đầu
          <input type="password" autoComplete="new-password" className="auth-input" {...register("password")} />
          {errors.password && <span className="text-red-600">{errors.password.message}</span>}
        </label>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <div>
        <button
          type="submit"
          disabled={isSubmitting}
          className="rounded-[3px] bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:opacity-60"
        >
          {isSubmitting ? "Đang tạo..." : "Tạo tài khoản"}
        </button>
      </div>
    </form>
  );
}

function ResetPasswordForm({ user, onDone }: { user: AdminUser; onDone: (message: string) => void }) {
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<{ password: string }>({ resolver: zodResolver(resetPasswordSchema) });

  async function onSubmit(values: { password: string }) {
    setError(null);
    try {
      await resetUserPassword(user.id, values.password);
      onDone(`Đã đặt lại mật khẩu cho ${user.email}. Các phiên đăng nhập cũ của họ đã bị đóng.`);
    } catch (err) {
      setError(apiErrorMessage(err, "Đặt lại mật khẩu thất bại"));
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="mt-3 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="password"
          autoComplete="new-password"
          aria-label={`Mật khẩu mới của ${user.email}`}
          placeholder="Mật khẩu mới"
          className="auth-input min-w-0 flex-1"
          {...register("password")}
        />
        <button
          type="submit"
          disabled={isSubmitting}
          className="rounded-[3px] bg-ink px-3 py-1.5 text-sm font-semibold text-white hover:bg-ink-raised disabled:opacity-60"
        >
          {isSubmitting ? "Đang lưu..." : "Lưu mật khẩu mới"}
        </button>
      </div>
      {errors.password && <span className="text-sm text-red-600">{errors.password.message}</span>}
      {error && <span className="text-sm text-red-700">{error}</span>}
    </form>
  );
}
