import { useEffect, useState } from "react";
import {
  apiErrorMessage,
  getMoodleAccount,
  linkMoodleAccount,
  MOODLE_ACCOUNT_CHANGED_EVENT,
  type MoodleAccountStatus,
} from "../api";

export type MoodleAccountState = MoodleAccountStatus | "unknown";

const LABEL: Record<MoodleAccountState, string> = {
  linked: "Đã liên kết",
  not_linked: "Chưa liên kết",
  ambiguous: "Chưa liên kết: có nhiều tài khoản Moodle cùng email của bạn, hãy nhờ admin Moodle xử lý trùng email",
  unknown: "Không kiểm tra được Moodle",
};

// The lecturer's own Moodle account, always on screen where Moodle is used: whether it is
// linked, and a "Liên kết tài khoản Moodle" button usable at any time (it links the Moodle user
// with their email, or creates it). Asks the server again whenever the account may have changed
// (MOODLE_ACCOUNT_CHANGED_EVENT), e.g. when a Moodle action was refused because the link must
// be made again. `onStatus` tells the page what the server answered.
export function MoodleAccountSection({ onStatus }: { onStatus?: (status: MoodleAccountState) => void }) {
  const [status, setStatus] = useState<MoodleAccountState>("unknown");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);

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

  async function handleLink() {
    setBusy(true);
    setError(null);
    try {
      await linkMoodleAccount();
      setStatus("linked");
    } catch (err) {
      setError(apiErrorMessage(err, "Liên kết tài khoản Moodle thất bại"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Tài khoản Moodle" className="mb-4 rounded-[3px] border border-gray-200 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-medium">Tài khoản Moodle:</span>{" "}
          <span className={status === "linked" ? "text-green-700" : "text-gray-600"}>{LABEL[status]}</span>
        </p>
        <button
          onClick={handleLink}
          disabled={busy}
          className="whitespace-nowrap rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
        >
          {busy ? "Đang liên kết..." : "Liên kết tài khoản Moodle"}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </section>
  );
}
