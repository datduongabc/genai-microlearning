import { useEffect, useState } from "react";
import { getMoodleAccount, MOODLE_ACCOUNT_CHANGED_EVENT, type MoodleAccountStatus } from "../api";

export type MoodleAccountState = MoodleAccountStatus | "unknown";

const LABEL: Record<MoodleAccountState, string> = {
  linked: "Đã liên kết",
  not_linked: "Chưa liên kết",
  unknown: "Không kiểm tra được Moodle",
};

// The lecturer's own Moodle account, always on screen where Moodle is used. Read only: it is linked
// when they log in with their Moodle username and password (the server makes it then), so when it is
// not, the way to link it is to log in again. Asks the server again whenever the account may have
// changed (MOODLE_ACCOUNT_CHANGED_EVENT), e.g. when a Moodle action was refused because the link is
// gone. `onStatus` tells the page what the server answered.
export function MoodleAccountSection({ onStatus }: { onStatus?: (status: MoodleAccountState) => void }) {
  const [status, setStatus] = useState<MoodleAccountState>("unknown");
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

  return (
    <section aria-label="Tài khoản Moodle" className="mb-4 rounded-[3px] border border-gray-200 p-4">
      <p className="text-sm">
        <span className="font-medium">Tài khoản Moodle:</span>{" "}
        <span className={status === "linked" ? "text-green-700" : "text-gray-600"}>{LABEL[status]}</span>
      </p>
      {status === "not_linked" && (
        <p className="mt-2 text-sm text-gray-600">
          Tài khoản Moodle được liên kết khi bạn đăng nhập bằng tên đăng nhập và mật khẩu Moodle. Hãy đăng xuất rồi
          đăng nhập lại bằng tài khoản Moodle để liên kết.
        </p>
      )}
    </section>
  );
}
