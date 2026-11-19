import { Ellipsis, KeyRound, Unlink, UserCheck, UserX } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { confirmAction } from "../lib/confirm";
import { notifyDone, notifyFailed } from "../lib/notify";
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
  const [role, setRole] = useState<"all" | "admin" | "lecturer">("all");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);

  // Bumped to load the list again (after a change), so the effect below is the only loader.
  const [reloads, setReloads] = useState(0);
  const reload = useCallback(() => {
    setReloads((count) => count + 1);
    onChange();
  }, [onChange]);


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
    const verb = user.is_active ? "Vô hiệu hóa" : "Kích hoạt lại";
    if (!await confirmAction({ title: `${verb} tài khoản ${user.email}?`, description: "Dữ liệu và liên kết Moodle được giữ nguyên.", action: verb, destructive: user.is_active })) return;
    setBusyId(user.id);
    try {
      await setUserActive(user.id, !user.is_active);
      notifyDone(user.is_active ? `Đã vô hiệu hóa tài khoản ${user.email}.` : `Đã kích hoạt lại tài khoản ${user.email}.`);
      reload();
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Đổi trạng thái tài khoản thất bại"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleUnlinkMoodle(user: AdminUser) {
    if (!await confirmAction({ title: `Hủy liên kết tài khoản Moodle của ${user.email}?`, description: "Các khóa học và nội dung trên Moodle được giữ nguyên. Người dùng cần liên kết lại để tiếp tục đẩy nội dung lên Moodle.", action: "Hủy liên kết" })) return;
    setBusyId(user.id);
    try {
      await unlinkUserMoodleAccount(user.id);
      notifyDone(`Đã hủy liên kết Moodle của ${user.email}.`);
      reload();
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Không hủy được liên kết Moodle."));
    } finally {
      setBusyId(null);
    }
  }

  const shown = users?.filter((u) => role === "all" || u.is_admin === (role === "admin")) ?? [];

  return (
    <div className="admin-users">
      <div className="admin-toolbar">
        <div role="group" aria-label="Trạng thái tài khoản" className="segmented">
          <button aria-pressed={filter === "active"} onClick={() => setFilter("active")}>Đang hoạt động{counts && <>{" "}<span className="count">{counts.users - counts.inactive_users}</span></>}</button>
          <button aria-pressed={filter === "inactive"} onClick={() => setFilter("inactive")}>Đã vô hiệu hóa{counts && <>{" "}<span className="count">{counts.inactive_users}</span></>}</button>
        </div>
        <label className="filter-field">
          Vai trò
          <select value={role} onChange={(e) => setRole(e.target.value as typeof role)} className="ui-select">
            <option value="all">Tất cả</option>
            <option value="lecturer">Giảng viên</option>
            <option value="admin">Quản trị viên</option>
          </select>
        </label>
        <span className="flex-1" />
        <button className="ui-button primary" aria-expanded={creating} onClick={() => setCreating(!creating)}>{creating ? "Đóng" : "+ Tạo tài khoản"}</button>
      </div>
      {creating && <CreateUserForm
        onCreated={(created) => {
          setCreating(false);
          notifyDone(`Đã tạo tài khoản cho ${created.email}.`);
          reload();
        }}
      />}
      {loadError && <p className="text-sm danger-text">{loadError}</p>}

      {users === null ? (
        !loadError && <p className="text-sm text-gray-500">Đang tải…</p>
      ) : (
        <ul className="account-table ui-card">
          <li className="account-row table-heading"><span>Người dùng</span><span>Vai trò</span><span>Moodle</span><span /></li>
          {shown.map((u) => (
            <li key={u.id}>
              <div className="account-row">
                <div className="flex min-w-0 items-center gap-3">
                  <span className={`avatar${u.is_admin ? " admin" : ""}`} aria-hidden="true">{initials(u.name)}</span>
                  <div className="min-w-0"><div className="font-semibold">{u.name}</div><div className="small muted break-all">{u.email}</div></div>
                </div>
                <span className="small">{u.is_admin ? "Quản trị viên" : "Giảng viên"}</span>
                <span className={`small ${u.moodle_status === "linked" ? "success-text" : "muted"}`}>
                  {u.moodle_status === null ? "—" : u.moodle_status === "linked" ? "↗ Đã liên kết" : "Chưa liên kết"}
                </span>
                {/* Not modal: the rest of the list stays usable, as it was, and a press outside closes it. */}
                <DropdownMenu modal={false}>
                  <DropdownMenuTrigger className="icon-button justify-self-end" aria-label={`Thao tác ${u.email}`}>
                    <Ellipsis aria-hidden="true" />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="doc-menu">
                    <DropdownMenuItem onSelect={() => setResetting(resetting === u.id ? null : u.id)}>
                      <KeyRound aria-hidden="true" />Đặt lại mật khẩu
                    </DropdownMenuItem>
                    {u.moodle_status === "linked" && <DropdownMenuItem disabled={busyId === u.id} onSelect={() => void handleUnlinkMoodle(u)}>
                      <Unlink aria-hidden="true" />Hủy liên kết Moodle
                    </DropdownMenuItem>}
                    {/* An admin account cannot be deactivated, so the item is left out rather than shown dead. */}
                    {!u.is_admin && <DropdownMenuItem variant={u.is_active ? "destructive" : "default"} disabled={busyId === u.id} onSelect={() => void handleToggleActive(u)}>
                      {u.is_active ? <UserX aria-hidden="true" /> : <UserCheck aria-hidden="true" />}{u.is_active ? "Vô hiệu hóa" : "Kích hoạt lại"}
                    </DropdownMenuItem>}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              {resetting === u.id && (
                <ResetPasswordForm
                  user={u}
                  onDone={(message) => {
                    setResetting(null);
                    notifyDone(message);
                  }}
                />
              )}
            </li>
          ))}
          {shown.length === 0 && <li className="account-empty">Chưa có tài khoản nào.</li>}
        </ul>
      )}
    </div>
  );
}

function CreateUserForm({ onCreated }: { onCreated: (user: AdminUser) => void }) {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CreateUserFormValues>({ resolver: zodResolver(createUserSchema) });

  async function onSubmit(values: CreateUserFormValues) {
    try {
      const created = await createUser(values.name, values.email, values.password);
      reset();
      onCreated(created);
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Tạo tài khoản thất bại"));
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
          {errors.name && <span className="danger-text">{errors.name.message}</span>}
        </label>
        <label className="flex flex-col gap-1 text-sm text-gray-500">
          Email
          <input type="email" autoComplete="off" className="auth-input" {...register("email")} />
          {errors.email && <span className="danger-text">{errors.email.message}</span>}
        </label>
        <label className="flex flex-col gap-1 text-sm text-gray-500">
          Mật khẩu ban đầu
          <input type="password" autoComplete="new-password" className="auth-input" {...register("password")} />
          {errors.password && <span className="danger-text">{errors.password.message}</span>}
        </label>
      </div>
      <div>
        <button
          type="submit"
          disabled={isSubmitting}
          className="ui-button primary"
        >
          {isSubmitting ? "Đang tạo…" : "Tạo tài khoản"}
        </button>
      </div>
    </form>
  );
}

function ResetPasswordForm({ user, onDone }: { user: AdminUser; onDone: (message: string) => void }) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<{ password: string }>({ resolver: zodResolver(resetPasswordSchema) });

  async function onSubmit(values: { password: string }) {
    try {
      await resetUserPassword(user.id, values.password);
      onDone(`Đã đặt lại mật khẩu cho ${user.email}. Các phiên đăng nhập cũ của họ đã bị đóng.`);
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Đặt lại mật khẩu thất bại"));
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
          className="ui-button primary"
        >
          {isSubmitting ? "Đang lưu…" : "Lưu mật khẩu mới"}
        </button>
      </div>
      {errors.password && <span className="text-sm danger-text">{errors.password.message}</span>}
    </form>
  );
}
