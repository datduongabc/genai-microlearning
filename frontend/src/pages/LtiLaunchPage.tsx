import { isAxiosError } from "axios";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Brand } from "../components/AuthLayout";

// Where an LTI launch from the LMS lands (backend/app/api/lti.py). The fragment carries a code
// that works once, for a minute, so it is read once and wiped from the address bar at once.
const MISSING_CODE = "Liên kết mở công cụ không hợp lệ. Hãy mở lại hoạt động từ Moodle.";

export function LtiLaunchPage() {
  const { loginWithLaunchCode } = useAuth();
  const navigate = useNavigate();
  // Read before the effect wipes it; a missing code is known at the first render.
  const [error, setError] = useState<string | null>(() =>
    new URLSearchParams(window.location.hash.slice(1)).get("code") ? null : MISSING_CODE);
  // StrictMode runs effects twice in development; a second exchange would spend nothing and fail.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const code = new URLSearchParams(window.location.hash.slice(1)).get("code");
    // Keep the router's index in the state: it is what lets the leave guard see a Back.
    window.history.replaceState(window.history.state, "", window.location.pathname);
    if (!code) return;
    loginWithLaunchCode(code)
      .then(({ courseId, owner }) =>
        navigate(owner ? `/courses/${courseId}` : `/learn/${courseId}`, { replace: true }))
      .catch((err: unknown) => {
        const detail = isAxiosError(err) ? err.response?.data?.detail : null;
        setError(typeof detail === "string" ? detail : "Không mở được công cụ. Hãy mở lại hoạt động từ Moodle.");
      });
  }, [loginWithLaunchCode, navigate]);

  return (
    <main className="launch-page">
      <Brand />
      {error ? (
        <div role="alert" className="ui-alert">
          {error}
        </div>
      ) : (
        <p role="status" className="launch-status">Đang mở khóa học…</p>
      )}
    </main>
  );
}
