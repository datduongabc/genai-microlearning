import { useEffect, useState } from "react";
import { apiErrorMessage, getMoodleAccount, linkMoodleAccount } from "../api";

// Asked once per browser session: "Không" is remembered until the tab is closed, and the
// account can still be linked later from the course page.
const DISMISSED_KEY = "moodle-account-prompt-dismissed";

function wasDismissed(): boolean {
  try {
    return sessionStorage.getItem(DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

function remember() {
  try {
    sessionStorage.setItem(DISMISSED_KEY, "1");
  } catch {
    // Without storage the question is simply asked again next time.
  }
}

// Recommends a lecturer whose Moodle account is not linked yet to link it. Shown by the page
// shell only to lecturers; renders nothing for anyone already linked or when Moodle is out of reach.
export function MoodleAccountPrompt() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (wasDismissed()) return;
    let cancelled = false;
    Promise.resolve(getMoodleAccount()).then(
      (answer) => {
        if (!cancelled && answer?.status === "not_linked") setOpen(true);
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
      setOpen(false);
    } catch (err) {
      setError(apiErrorMessage(err, "Liên kết tài khoản Moodle thất bại"));
    } finally {
      setBusy(false);
    }
  }

  function handleNo() {
    remember();
    setOpen(false);
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
          Tài khoản của bạn chưa liên kết với Moodle. Nên liên kết để đẩy môn học lên Moodle với đúng tên
          giảng viên: tài khoản Moodle có cùng email sẽ được liên kết, nếu chưa có thì hệ thống tạo mới.
          Bạn cần liên kết trước khi liên kết một course với Moodle.
        </p>
        {error && <p className="mb-3 text-sm text-red-700">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <button
            onClick={handleNo}
            disabled={busy}
            className="rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-60"
          >
            Không
          </button>
          <button
            onClick={handleYes}
            disabled={busy}
            className="rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
          >
            {busy ? "Đang liên kết..." : "Có (khuyến nghị)"}
          </button>
        </div>
      </div>
    </div>
  );
}
