import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  apiErrorMessage,
  createLecturer,
  listUsers,
  resetUserPassword,
  setUserActive,
  type AdminUser,
  type MoodleStatus,
} from "../api";
import { useAuth } from "../auth/useAuth";
import {
  createLecturerSchema,
  resetPasswordSchema,
  type CreateLecturerFormValues,
} from "../lib/authSchemas";

const MOODLE_LABEL: Record<MoodleStatus, { text: string; color: string }> = {
  linked: { text: "Đã liên kết Moodle", color: "text-green-700" },
  not_found: { text: "Chưa có tài khoản Moodle cùng email", color: "text-amber-700" },
  ambiguous: { text: "Nhiều tài khoản Moodle trùng email", color: "text-red-700" },
  unknown: { text: "Không kiểm tra được Moodle", color: "text-gray-500" },
};

const inputClass =
  "rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-terracotta";

// What the admin does with lecturers' accounts: create, lock and unlock, set a new password. A
// lock never deletes anything, and there is no way to delete an account.
export function AdminUsersPanel() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await listUsers());
      setLoadError(null);
    } catch (err) {
      setLoadError(apiErrorMessage(err, "Không tải được danh sách tài khoản."));
    }
  }, []);

  useEffect(() => {
    // Sets state only from the promise callbacks inside load().
    void load();
  }, [load]);

  async function handleToggleActive(user: AdminUser) {
    setBusyId(user.id);
    setError(null);
    setNotice(null);
    try {
      await setUserActive(user.id, !user.is_active);
      setNotice(user.is_active ? `Đã khóa tài khoản ${user.email}.` : `Đã mở khóa tài khoản ${user.email}.`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, "Đổi trạng thái tài khoản thất bại"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <CreateLecturerForm
        onCreated={async (created) => {
          setError(null);
          setNotice(`Đã tạo tài khoản cho ${created.email}.`);
          await load();
        }}
      />

      {notice && <p className="text-sm text-green-800">{notice}</p>}
      {error && <p className="text-sm text-red-700">{error}</p>}
      {loadError && <p className="text-sm text-red-700">{loadError}</p>}

      {users === null ? (
        !loadError && <p className="text-sm text-gray-500">Đang tải...</p>
      ) : (
        <ul className="flex flex-col divide-y divide-gray-200">
          {users.map((u) => (
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
                    {u.moodle_status && (
                      <span className={MOODLE_LABEL[u.moodle_status].color}>
                        {MOODLE_LABEL[u.moodle_status].text}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-4 text-sm">
                  <button
                    onClick={() => setResetting(resetting === u.id ? null : u.id)}
                    className="font-semibold text-terracotta hover:underline"
                  >
                    Đặt lại mật khẩu
                  </button>
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
          {users.length === 0 && <p className="py-4 text-sm text-gray-500">Chưa có tài khoản nào.</p>}
        </ul>
      )}
    </div>
  );
}

function CreateLecturerForm({ onCreated }: { onCreated: (user: AdminUser) => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CreateLecturerFormValues>({ resolver: zodResolver(createLecturerSchema) });

  async function onSubmit(values: CreateLecturerFormValues) {
    setError(null);
    try {
      const created = await createLecturer(values.name, values.email, values.password);
      reset();
      await onCreated(created);
    } catch (err) {
      setError(apiErrorMessage(err, "Tạo tài khoản thất bại"));
    }
  }

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      className="flex flex-col gap-3 rounded-[3px] border border-gray-200 p-4"
    >
      <h2 className="text-sm font-semibold">Tạo tài khoản giảng viên</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs text-gray-500">
          Họ và tên
          <input type="text" autoComplete="off" className={inputClass} {...register("name")} />
          {errors.name && <span className="text-red-600">{errors.name.message}</span>}
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-500">
          Email
          <input type="email" autoComplete="off" className={inputClass} {...register("email")} />
          {errors.email && <span className="text-red-600">{errors.email.message}</span>}
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-500">
          Mật khẩu ban đầu
          <input type="password" autoComplete="new-password" className={inputClass} {...register("password")} />
          {errors.password && <span className="text-red-600">{errors.password.message}</span>}
        </label>
      </div>
      <p className="text-xs text-gray-500">
        Email phải trùng với email tài khoản Moodle của giảng viên để môn học được đẩy lên Moodle đúng người.
      </p>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <div>
        <button
          type="submit"
          disabled={isSubmitting}
          className="rounded-[3px] bg-terracotta px-4 py-2 text-sm font-semibold text-white hover:bg-terracotta-dark disabled:opacity-60"
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
          className={`${inputClass} min-w-0 flex-1`}
          {...register("password")}
        />
        <button
          type="submit"
          disabled={isSubmitting}
          className="rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
        >
          {isSubmitting ? "Đang lưu..." : "Lưu mật khẩu mới"}
        </button>
      </div>
      {errors.password && <span className="text-xs text-red-600">{errors.password.message}</span>}
      {error && <span className="text-sm text-red-700">{error}</span>}
    </form>
  );
}
