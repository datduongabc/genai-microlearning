import { useEffect, useState } from "react";
import {
  apiErrorMessage,
  dismissMoodlePrompt,
  getMoodleAccount,
  linkMoodleAccount,
  type MoodleAccountStatus,
} from "../api";

// Recommends a lecturer whose Moodle account is not linked yet to link it, even when Moodle
// already has a user with their email: only their "Có" links it. When several Moodle users share
// the email it says so instead, since none can be linked. Shown by the page shell only to
// lecturers, and only while the server says to (`prompt`): once per lecturer, until they answer
// "Có" or "Không", which the server keeps with their account. Afterwards the account is linked
// from the course page's "Tài khoản Moodle" section. Renders nothing when Moodle is out of reach.
export function MoodleAccountPrompt() {
  const [status, setStatus] = useState<Exclude<MoodleAccountStatus, "linked"> | null>(null);
  const open = status !== null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.resolve(getMoodleAccount()).then(
      (answer) => {
        if (!cancelled && answer?.prompt && (answer.status === "not_linked" || answer.status === "ambiguous")) {
          setStatus(answer.status);
        }
      },
      () => {
        // Moodle unreachable: nothing to recommend; the course page says so when it matters.
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (!open) return null;

  async function handleYes() {
    setBusy(true);
    setError(null);
    try {
      await linkMoodleAccount();
      setStatus(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Liên kết tài khoản Moodle thất bại"));
    } finally {
      setBusy(false);
    }
  }

  function handleNo() {
    setStatus(null);
    // Should the answer not reach the server, the pop-up is simply asked again next time.
    Promise.resolve(dismissMoodlePrompt()).catch(() => {});
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="moodle-account-prompt-title"
        className="w-full max-w-md rounded-[3px] bg-white p-6 shadow-lg"
      >
        <h2 id="moodle-account-prompt-title" className="mb-2 font-serif text-lg font-semibold">
          Liên kết tài khoản Moodle?
        </h2>
        <p className="mb-4 text-sm text-gray-600">
          {status === "ambiguous"
            ? "Tài khoản của bạn chưa liên kết với Moodle, và có nhiều tài khoản Moodle cùng email của bạn nên không xác định được tài khoản để liên kết. Hãy nhờ admin Moodle xử lý trùng email rồi liên kết từ trang môn học."
            : "Tài khoản của bạn chưa liên kết với Moodle. Nên liên kết để đẩy môn học lên Moodle với đúng tên giảng viên: tài khoản Moodle có cùng email sẽ được liên kết, nếu chưa có thì hệ thống tạo mới. Bạn cần liên kết trước khi liên kết một course với Moodle."}
        </p>
        {error && <p className="mb-3 text-sm text-red-700">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <button
            onClick={handleNo}
            disabled={busy}
            className="rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-60"
          >
            {status === "ambiguous" ? "Đóng" : "Không"}
          </button>
          {status === "not_linked" && (
            <button
              onClick={handleYes}
              disabled={busy}
              className="rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
            >
              {busy ? "Đang liên kết..." : "Có (khuyến nghị)"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
