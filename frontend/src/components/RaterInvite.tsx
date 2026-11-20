import { X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { listUsers, type AdminUser } from "../api/admin";

const SHOWN = 8;

// Raters are picked from the active accounts, so an email with no account cannot be invited.
// A pasted list adds the emails that have one and names the rest.
export function RaterInvite({ emails, onChange, disabled }: {
  emails: string[]; onChange: (emails: string[]) => void; disabled: boolean;
}) {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const [notFound, setNotFound] = useState<string[]>([]);
  const [error, setError] = useState("");
  const list = useId();

  useEffect(() => {
    let alive = true;
    listUsers(true).then((data) => { if (alive) setUsers(data); })
      .catch(() => { if (alive) setError("Không tải được danh sách tài khoản."); });
    return () => { alive = false; };
  }, []);

  const byEmail = new Map((users ?? []).map((u) => [u.email.toLowerCase(), u]));
  const q = query.trim().toLowerCase();
  const matches = q ? (users ?? []).filter((u) => !emails.includes(u.email.toLowerCase())
    && (u.email.toLowerCase().includes(q) || u.name.toLowerCase().includes(q))).slice(0, SHOWN) : [];

  function add(more: string[]) {
    const next = [...new Set([...emails, ...more])];
    if (next.length > 100) { setError("Tối đa 100 người chấm."); return; }
    onChange(next); setQuery(""); setActive(0); setError("");
  }
  function pick(user: AdminUser) { setNotFound([]); add([user.email.toLowerCase()]); }

  return <div>
    <div className="rater-chips">
      {emails.map((email) => <span className="rater-chip" key={email} title={byEmail.get(email)?.name}>{email}
        <button type="button" aria-label={`Bỏ ${email}`} disabled={disabled} onClick={() => onChange(emails.filter((e) => e !== email))}>
          <X aria-hidden="true" className="mx-auto size-3.5" />
        </button>
      </span>)}
      <div className="rater-search">
        <input id="rater-email" type="text" role="combobox" autoComplete="off" disabled={disabled || !users}
          placeholder={users ? "Tìm theo tên hoặc email" : "Đang tải tài khoản…"} value={query}
          aria-expanded={open && q !== ""} aria-controls={list} aria-autocomplete="list"
          aria-activedescendant={matches[active] ? `${list}-${active}` : undefined}
          onChange={(e) => { setQuery(e.target.value); setActive(0); setOpen(true); }}
          onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, matches.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
            else if (e.key === "Enter") { e.preventDefault(); if (matches[active]) pick(matches[active]); }
            else if (e.key === "Escape") setOpen(false);
            else if (e.key === "Backspace" && query === "" && emails.length) onChange(emails.slice(0, -1));
          }}
          onPaste={(e) => {
            const words = e.clipboardData.getData("text/plain").toLowerCase().split(/[\s,;]+/).filter(Boolean);
            if (words.length < 2 && !words[0]?.includes("@")) return;
            e.preventDefault();
            const found = words.filter((w) => byEmail.has(w));
            setNotFound(words.filter((w) => !byEmail.has(w)));
            add(found);
          }} />
        {open && q !== "" && <ul id={list} role="listbox" aria-label="Tài khoản" className="rater-options">
          {matches.map((user, i) => <li key={user.id} id={`${list}-${i}`} role="option" aria-selected={i === active}
            onMouseDown={(e) => { e.preventDefault(); pick(user); }} onMouseEnter={() => setActive(i)}>
            <span className="font-medium">{user.name}</span>
            <span className="small muted [overflow-wrap:anywhere]">{user.email}</span>
          </li>)}
          {matches.length === 0 && <li className="small muted rater-options-empty">Không có tài khoản nào khớp.</li>}
        </ul>}
      </div>
    </div>
    {notFound.length > 0 && <p role="alert" className="small mt-2">Chưa có tài khoản: {notFound.join(", ")}</p>}
    {error && <p role="alert" className="small mt-2">{error}</p>}
  </div>;
}
