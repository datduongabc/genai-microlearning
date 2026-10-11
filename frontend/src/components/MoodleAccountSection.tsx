import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { apiErrorMessage, getMoodleAccount, requestMoodleLink, unlinkMoodleAccount, MOODLE_ACCOUNT_CHANGED_EVENT,
  type MoodleAccount, type MoodleAccountStatus } from "../api";

export type MoodleAccountState = MoodleAccountStatus | "unknown";
const emailSchema = z.object({ email: z.string().trim().email("Email không hợp lệ.") });
const LABEL: Record<MoodleAccountState, string> = {
  linked: "Đã liên kết", not_linked: "Chưa liên kết", unknown: "Không kiểm tra được Moodle",
  pending: "Chờ xác nhận trong Moodle", rejected: "Yêu cầu đã bị từ chối", expired: "Yêu cầu đã hết hạn",
};

export function MoodleAccountSection({ onStatus }: { onStatus?: (status: MoodleAccountState) => void }) {
  const [answer, setAnswer] = useState<MoodleAccount | null>(null);
  const [unlinking, setUnlinking] = useState(false);
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

  async function unlink() {
    if (!window.confirm("Hủy liên kết tài khoản Moodle? Các khóa học và nội dung trên Moodle được giữ nguyên. Bạn cần liên kết lại tài khoản để tiếp tục đẩy nội dung lên Moodle.")) return;
    requestVersion.current += 1;
    setUnlinking(true);
    setError(null);
    try {
      await unlinkMoodleAccount();
      setReloads((n) => n + 1);
    } catch (err) {
      setError(apiErrorMessage(err, "Không hủy được liên kết Moodle."));
    } finally {
      setUnlinking(false);
    }
  }

  return (
    <section aria-label="Tài khoản Moodle" className="mb-4 rounded-[3px] border border-gray-200 p-4">
      <p className="text-sm">
        <span className="font-medium">Tài khoản Moodle:</span>{" "}
        <span className={status === "linked" ? "text-green-700" : "text-gray-600"}>{LABEL[status]}</span>
      </p>
      {answer?.email && <p className="mt-1 text-sm text-gray-600">{answer.email}</p>}
      {status === "linked" && (
        <button type="button" disabled={unlinking} onClick={() => void unlink()} className="mt-2 text-sm text-red-700 hover:underline disabled:opacity-60">
          {unlinking ? "Đang hủy liên kết..." : "Hủy liên kết Moodle"}
        </button>
      )}
      {status === "pending" && <p className="mt-2 text-sm text-gray-600">Nếu có tài khoản Moodle phù hợp, hãy đăng nhập Moodle để xác nhận liên kết.</p>}
      {status === "pending" && answer?.confirmation_url && (
        <a href={answer.confirmation_url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-sm text-terracotta underline">
          Mở Moodle để xác nhận
        </a>
      )}
      {status !== "linked" && status !== "unknown" && (
        <form onSubmit={(event) => { void handleSubmit(submit)(event); }} noValidate className="mt-3 flex flex-col gap-2">
          <p className="text-sm text-gray-600">Tài khoản trong ứng dụng và tài khoản Moodle được quản lý riêng. Nhập email Moodle. Nếu có tài khoản Moodle phù hợp, hãy đăng nhập Moodle để xác nhận yêu cầu. Liên kết chỉ hoạt động sau khi bạn xác nhận.</p>
          <label className="text-sm">Email Moodle
            <input type="email" autoComplete="email" {...register("email")} className="ml-2 rounded border border-gray-300 px-3 py-1.5" />
          </label>
          {errors.email && <p className="text-sm text-red-700">{errors.email.message}</p>}
          <button disabled={isSubmitting} className="self-start rounded bg-terracotta px-3 py-2 text-sm text-white disabled:opacity-60">
            {isSubmitting ? "Đang gửi..." : "Yêu cầu liên kết"}
          </button>
        </form>
      )}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </section>
  );
}
