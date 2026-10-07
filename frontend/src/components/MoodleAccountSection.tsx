import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  apiErrorMessage,
  getMoodleAccount,
  linkMoodleAccount,
  MOODLE_ACCOUNT_CHANGED_EVENT,
  type MoodleAccountStatus,
} from "../api";
import { linkMoodleSchema, type LinkMoodleFormValues } from "../lib/authSchemas";

export type MoodleAccountState = MoodleAccountStatus | "unknown";

const LABEL: Record<MoodleAccountState, string> = {
  linked: "Đã liên kết",
  not_linked: "Chưa liên kết",
  unknown: "Không kiểm tra được Moodle",
};

const INPUT =
  "w-full rounded-[3px] border border-gray-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-terracotta";

// The lecturer's own Moodle account, always on screen where Moodle is used: whether it is linked,
// and a form for the Moodle username and password that links it (the server asks Moodle whether
// they are right; the password is used for that one check and never kept). An admin makes the
// Moodle accounts, so there is nothing to create here. Asks the server again whenever the account
// may have changed (MOODLE_ACCOUNT_CHANGED_EVENT), e.g. when a Moodle action was refused because
// the link must be made again. `onStatus` tells the page what the server answered.
export function MoodleAccountSection({ onStatus }: { onStatus?: (status: MoodleAccountState) => void }) {
  const [status, setStatus] = useState<MoodleAccountState>("unknown");
  const [error, setError] = useState<string | null>(null);
  const [relinking, setRelinking] = useState(false);
  const [reloads, setReloads] = useState(0);

  const {
    register,
    handleSubmit,
    reset,
    resetField,
    formState: { errors, isSubmitting },
  } = useForm<LinkMoodleFormValues>({ resolver: zodResolver(linkMoodleSchema) });

  useEffect(() => {
    let cancelled = false;
    const reload = () => setReloads((n) => n + 1);
    window.addEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, reload);
    Promise.resolve(getMoodleAccount()).then(
      (answer) => {
        if (!cancelled) setStatus(answer?.status ?? "unknown");
      },
      () => {
        if (!cancelled) setStatus("unknown");
      },
    );
    return () => {
      cancelled = true;
      window.removeEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, reload);
    };
  }, [reloads]);

  useEffect(() => {
    onStatus?.(status);
  }, [status, onStatus]);

  async function onSubmit(values: LinkMoodleFormValues) {
    setError(null);
    try {
      await linkMoodleAccount(values.username, values.password);
      setStatus("linked");
      setRelinking(false);
      reset();
    } catch (err) {
      setError(apiErrorMessage(err, "Liên kết tài khoản Moodle thất bại"));
      resetField("password");
    }
  }

  const showForm = status !== "linked" || relinking;

  return (
    <section aria-label="Tài khoản Moodle" className="mb-4 rounded-[3px] border border-gray-200 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-medium">Tài khoản Moodle:</span>{" "}
          <span className={status === "linked" ? "text-green-700" : "text-gray-600"}>{LABEL[status]}</span>
        </p>
        {status === "linked" && !relinking && (
          <button
            type="button"
            onClick={() => setRelinking(true)}
            className="whitespace-nowrap rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
          >
            Liên kết tài khoản khác
          </button>
        )}
      </div>
      {showForm && (
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="mt-3 flex flex-col gap-3">
          <p className="text-sm text-gray-600">
            Nhập tên đăng nhập và mật khẩu Moodle của bạn. Tài khoản Moodle do admin tạo trước trên Moodle; mật khẩu chỉ
            dùng để kiểm tra một lần và không được lưu.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-gray-600">Tên đăng nhập Moodle</span>
              <input type="text" autoComplete="off" className={INPUT} {...register("username")} />
              {errors.username && <span className="text-xs text-red-600">{errors.username.message}</span>}
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-gray-600">Mật khẩu Moodle</span>
              <input type="password" autoComplete="off" className={INPUT} {...register("password")} />
              {errors.password && <span className="text-xs text-red-600">{errors.password.message}</span>}
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={isSubmitting}
              className="whitespace-nowrap rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
            >
              {isSubmitting ? "Đang kiểm tra..." : "Liên kết tài khoản Moodle"}
            </button>
            {relinking && (
              <button
                type="button"
                onClick={() => {
                  setRelinking(false);
                  setError(null);
                  reset();
                }}
                className="rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
              >
                Hủy
              </button>
            )}
          </div>
        </form>
      )}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </section>
  );
}
