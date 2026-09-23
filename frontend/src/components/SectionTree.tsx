import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  apiErrorMessage,
  generateModulesFromSections,
  listSections,
  type ModuleSummary,
  type SectionInfo,
} from "../api";

// Mirrors MAX_SECTIONS_PER_REQUEST in backend/app/api/modules.py: each module
// costs about 17 LLM calls, so this caps how much one click can push into the
// free tier's rate limit.
const MAX_SECTIONS_PER_REQUEST = 5;
const POLL_INTERVAL_MS = 3000;

interface SectionTreeProps {
  documentId: string;
  // Flips when generation for the document starts or ends, so the per-section
  // state is reloaded at those moments.
  documentGenerating: boolean;
  modules: ModuleSummary[];
  onGenerated: () => void;
}

// A picked section already contains its sub-sections and sits inside its
// parents, so picking one of those too would generate the same text twice.
function lockedSectionIds(sections: SectionInfo[], selected: Set<string>): Set<string> {
  const parentOf = new Map(sections.map((s) => [s.id, s.parent_id]));
  const locked = new Set<string>();
  for (const id of selected) {
    let parent = parentOf.get(id) ?? null;
    while (parent) {
      locked.add(parent);
      parent = parentOf.get(parent) ?? null;
    }
  }
  for (const section of sections) {
    let parent = section.parent_id;
    while (parent) {
      if (selected.has(parent)) {
        locked.add(section.id);
        break;
      }
      parent = parentOf.get(parent) ?? null;
    }
  }
  return locked;
}

// Pages are the physical pages of the file, which can differ from the numbers
// printed in the book.
function pageRange(section: SectionInfo): string {
  if (section.page_start === null) return "";
  return section.page_end !== null && section.page_end !== section.page_start
    ? ` (trang ${section.page_start}-${section.page_end})`
    : ` (trang ${section.page_start})`;
}

export function SectionTree({ documentId, documentGenerating, modules, onGenerated }: SectionTreeProps) {
  const [sections, setSections] = useState<SectionInfo[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Reloads when the document itself starts or stops generating, and drops a
  // response that arrives after the tree was closed or pointed at another document.
  useEffect(() => {
    let cancelled = false;
    listSections(documentId).then(
      (data) => {
        if (cancelled) return;
        setSections(data);
        setLoadError(null);
      },
      () => {
        if (!cancelled) setLoadError("Không tải được mục lục, vui lòng thử lại.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, documentGenerating]);

  const anyGenerating = sections?.some((s) => s.generating) ?? false;
  useEffect(() => {
    if (!anyGenerating) return;
    const timer = setInterval(() => {
      // A failed poll keeps what is shown; the next tick retries.
      listSections(documentId).then(setSections, () => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [anyGenerating, documentId]);

  const locked = useMemo(() => lockedSectionIds(sections ?? [], selected), [sections, selected]);
  const tooMany = selected.size > MAX_SECTIONS_PER_REQUEST;

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  async function handleGenerate() {
    // Document order, not click order, so the jobs run top to bottom.
    const ids = (sections ?? []).filter((s) => selected.has(s.id)).map((s) => s.id);
    setBusy(true);
    setError(null);
    try {
      await generateModulesFromSections(documentId, ids);
      setSelected(new Set());
      onGenerated();
      setSections(await listSections(documentId));
    } catch (err) {
      setError(apiErrorMessage(err, "Sinh mô-đun thất bại"));
    } finally {
      setBusy(false);
    }
  }

  if (loadError && sections === null) {
    return <p className="mt-3 text-sm text-red-700">{loadError}</p>;
  }
  if (sections === null) {
    return <p className="mt-3 text-sm text-gray-500">Đang tải mục lục...</p>;
  }

  return (
    <div className="mt-3 rounded-[3px] border border-gray-200 p-4">
      <p className="mb-3 text-xs text-gray-500">
        Mỗi mục được chọn sinh một mô-đun riêng, chỉ từ nội dung của mục đó.
      </p>
      <ul className="flex flex-col gap-2">
        {sections.map((s) => {
          const own = modules.filter((m) => m.section_id === s.id);
          return (
            <li key={s.id} style={{ paddingLeft: `${(s.level - 1) * 20}px` }}>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4 accent-terracotta"
                  checked={selected.has(s.id)}
                  disabled={s.generating || locked.has(s.id) || busy}
                  onChange={() => toggle(s.id)}
                />
                <span>
                  {s.title}
                  <span className="text-gray-500">{pageRange(s)}</span>
                </span>
                {s.generating && (
                  <span className="text-xs font-medium text-amber-600">Đang sinh...</span>
                )}
              </label>
              {own.length > 0 && (
                <ul className="mt-1 flex flex-col gap-1 pl-6">
                  {own.map((m) => (
                    <li key={m.id}>
                      <Link to={`/modules/${m.id}`} className="text-sm hover:text-terracotta">
                        → {m.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={handleGenerate}
          disabled={busy || selected.size === 0 || tooMany}
          className="rounded-[3px] bg-terracotta px-4 py-2 text-sm font-semibold text-white hover:bg-terracotta-dark disabled:opacity-60"
        >
          {selected.size === 0 ? "Sinh mô-đun" : `Sinh mô-đun cho ${selected.size} mục`}
        </button>
        {tooMany && (
          <span className="text-xs text-red-700">
            Chỉ được chọn tối đa {MAX_SECTIONS_PER_REQUEST} mục mỗi lần.
          </span>
        )}
      </div>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
