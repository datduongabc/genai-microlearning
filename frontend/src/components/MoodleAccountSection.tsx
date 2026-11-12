import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { apiErrorMessage, getMoodleAccount, requestMoodleLink, MOODLE_ACCOUNT_CHANGED_EVENT,
  type MoodleAccount, type MoodleAccountStatus } from "../api";

export type MoodleAccountState = MoodleAccountStatus | "unknown";
const emailSchema = z.object({ email: z.string().trim().email("Email không hợp lệ.") });
const LABEL: Record<MoodleAccountState, string> = {
  linked: "Đã liên kết", not_linked: "Chưa liên kết", unknown: "Không kiểm tra được Moodle",
  pending: "Chờ xác nhận trong Moodle", rejected: "Yêu cầu đã bị từ chối", expired: "Yêu cầu đã hết hạn",
  unavailable: "Chưa nối với Moodle",
};

export function MoodleAccountSection({ onStatus }: { onStatus?: (status: MoodleAccountState) => void }) {
  const [answer, setAnswer] = useState<MoodleAccount | null>(null);
  const [reloads, setReloads] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const status = answer?.status ?? "unknown";
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<z.infer<typeof emailSchema>>({
    resolver: zodResolver(emailSchema),
  });

  useEffect(() => {
    let cancelled = false;
    const version = requestVersion.current;
    Promise.resolve(getMoodleAccount()).then(
      (result) => {
        if (cancelled || version !== requestVersion.current) return;
        setAnswer(result);
        setError(result.error ?? null);
      },
      (err) => {
        if (cancelled || version !== requestVersion.current) return;
        setAnswer(null);
        setError(apiErrorMessage(err, "Không kiểm tra được tài khoản Moodle."));
      },
    );
    return () => { cancelled = true; };
  }, [reloads]);

  useEffect(() => {
    const reload = () => setReloads((n) => n + 1);
    window.addEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, reload);
    return () => window.removeEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, reload);
  }, []);

  useEffect(() => {
    if (status !== "pending") return;
    const timer = window.setInterval(() => setReloads((n) => n + 1), 5000);
    return () => window.clearInterval(timer);
  }, [status]);

  useEffect(() => { onStatus?.(status); }, [status, onStatus]);

  async function submit(values: z.infer<typeof emailSchema>) {
    requestVersion.current += 1;
    setError(null);
    try { setAnswer(await requestMoodleLink(values.email)); }
    catch (err) { setError(apiErrorMessage(err, "Không gửi được yêu cầu liên kết Moodle.")); }
  }

  return (
    <section aria-label="Tài khoản Moodle" className="moodle-account">
      <p className="text-sm">
        <span className="moodle-key">Tài khoản Moodle:</span>{" "}
        <span className={status === "linked" ? "success-text" : "muted"}>{LABEL[status]}</span>
      </p>
      {answer?.email && status !== "unavailable" && <p className="mt-1 text-sm muted">{answer.email}</p>}
      {status === "unavailable" && (
        <p className="mt-1 text-sm muted">
          Chưa liên kết tài khoản hay xuất bản sang Moodle được. Mở từ Moodle (LTI) vẫn dùng bình thường.
        </p>
      )}
      {status === "pending" && <p className="mt-2 text-sm muted">Nếu có tài khoản Moodle phù hợp, hãy đăng nhập Moodle để xác nhận liên kết.</p>}
      {status === "pending" && answer?.confirmation_url && (
        <a href={answer.confirmation_url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-sm text-accent underline">
          Mở Moodle để xác nhận
        </a>
      )}
      {status !== "linked" && status !== "unknown" && status !== "unavailable" && (
        <form onSubmit={(event) => { void handleSubmit(submit)(event); }} noValidate className="mt-3 flex flex-col gap-2">
          <p className="text-sm muted">Nhập email Moodle của bạn, rồi đăng nhập Moodle để xác nhận liên kết.</p>
          <label className="text-sm">Email Moodle
            <input type="email" autoComplete="email" {...register("email")} className="ui-input ml-2" />
          </label>
          {errors.email && <p className="text-sm danger-text">{errors.email.message}</p>}
          <button disabled={isSubmitting} className="ui-button primary self-start">
            {isSubmitting ? "Đang gửi..." : "Yêu cầu liên kết"}
          </button>
        </form>
      )}
      {error && <p className="mt-2 text-sm danger-text">{error}</p>}
    </section>
  );
}
