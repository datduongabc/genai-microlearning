import axios from "axios";
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  apiErrorMessage,
  createMoodleCourseFor,
  deleteDocument,
  generateModule,
  getModuleJobStatus,
  linkMoodleCourse,
  listCourses,
  listDocuments,
  listModules,
  pollJobStatus,
  searchMoodleCourses,
  unlinkMoodleCourse,
  uploadDocument,
  type Course,
  type DocumentInfo,
  type DocumentKind,
  type ModuleSummary,
  type MoodleCourseSearchResult,
} from "../api";
import { AppShell } from "../components/AppShell";
import { ModelPicker } from "../components/ModelPicker";
import { SectionTree } from "../components/SectionTree";

const STATUS_LABEL: Record<string, string> = {
  uploaded: "Đã tải lên",
  processing: "Đang xử lý",
  chunked: "Đã chia đoạn",
  embedded: "Sẵn sàng",
  failed: "Lỗi",
};

// Server-side truth for "a document is still being processed" and "a module is
// still being generated": survives a page refresh, navigation and other tabs,
// unlike component or store state.
const DOCUMENT_POLL_INTERVAL_MS = 3000;

function isPipelineActive(doc: DocumentInfo): boolean {
  return doc.status === "uploaded" || doc.status === "processing";
}

const STATUS_COLOR: Record<string, string> = {
  uploaded: "text-gray-500",
  processing: "text-amber-600",
  chunked: "text-amber-600",
  embedded: "text-green-700",
  failed: "text-red-700",
};

interface CoursePageSetters {
  setCourse: (course: Course | null) => void;
  setDocuments: (documents: DocumentInfo[]) => void;
  setModulesByDoc: (modules: Record<string, ModuleSummary[]>) => void;
}

// Hands over the documents as soon as they arrive and their modules a moment
// later, so the list appears without waiting for one request per document.
async function loadCoursePage(courseId: string, apply: CoursePageSetters) {
  const [courses, docs] = await Promise.all([listCourses(), listDocuments(courseId)]);
  apply.setCourse(courses.find((c) => c.id === courseId) ?? null);
  apply.setDocuments(docs);
  const entries = await Promise.all(
    docs.map(async (d) => [d.id, await listModules(d.id)] as const),
  );
  apply.setModulesByDoc(Object.fromEntries(entries));
}

export function CourseDetailPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const [course, setCourse] = useState<Course | null>(null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  // Model cho các lượt sinh bấm từ trang này, dùng chung cho cả tài liệu lẫn từng mục;
  // chuỗi rỗng nghĩa là chưa chọn gì. ModelPicker điền sẵn mặc định đã lưu của giảng viên.
  const [modelAlias, setModelAlias] = useState("");
  const [modulesByDoc, setModulesByDoc] = useState<
    Record<string, ModuleSummary[]>
  >({});
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Only bridges the moment between clicking and the server reporting the
  // job (so a double-click cannot send two requests); after that, and after
  // a refresh or navigation, `doc.generating` from the server is the truth.
  const [startingIds, setStartingIds] = useState<Set<string>>(new Set());
  // Chosen per upload: only a textbook gets an outline detected on the server.
  const [kind, setKind] = useState<DocumentKind>("lecture");
  const [openOutlines, setOpenOutlines] = useState<Set<string>>(new Set());

  function toggleOutline(documentId: string) {
    setOpenOutlines((current) => {
      const next = new Set(current);
      if (!next.delete(documentId)) next.add(documentId);
      return next;
    });
  }

  const refresh = useCallback(async () => {
    if (!courseId) return;
    await loadCoursePage(courseId, { setCourse, setDocuments, setModulesByDoc });
  }, [courseId]);

  // The first load drops whatever arrives after the page has gone, and sets
  // state only from promise callbacks.
  useEffect(() => {
    let cancelled = false;
    const guarded =
      <T,>(set: (value: T) => void) =>
      (value: T) => {
        if (!cancelled) set(value);
      };
    const first = courseId
      ? loadCoursePage(courseId, {
          setCourse: guarded(setCourse),
          setDocuments: guarded(setDocuments),
          setModulesByDoc: guarded(setModulesByDoc),
        })
      : Promise.resolve();
    first.finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [courseId]);

  // One document at a time: the upload control stays busy for as long as the
  // server reports a document still uploaded/processing, and this polls until
  // that changes.
  const pipelineActive = documents.some(isPipelineActive);
  const uploadBusy = uploading || pipelineActive;
  const generatingOnServer = documents.some((d) => d.generating);
  const shouldPoll = pipelineActive || generatingOnServer;
  const isGenerating = (doc: DocumentInfo) => doc.generating || startingIds.has(doc.id);
  // A textbook with a detected outline is generated section by section, so the
  // whole-document button gives way to the outline.
  const hasOutline = (doc: DocumentInfo) => doc.kind === "textbook" && doc.section_count > 0;
  // While the outline is open it shows each module under its own section, so
  // only the modules that belong to no section are listed here.
  const listedModules = (doc: DocumentInfo) => {
    const modules = modulesByDoc[doc.id] ?? [];
    return openOutlines.has(doc.id) ? modules.filter((m) => m.section_id === null) : modules;
  };
  useEffect(() => {
    if (!shouldPoll || !courseId) return;
    const timer = setInterval(async () => {
      try {
        setDocuments(await listDocuments(courseId));
      } catch {
        // transient failure: the next tick retries
      }
    }, DOCUMENT_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [shouldPoll, courseId]);

  // The module a finished generation produced only shows up in the per-document
  // module lists, which the polling above does not reload.
  const wasGeneratingOnServer = useRef(false);
  useEffect(() => {
    if (wasGeneratingOnServer.current && !generatingOnServer) void refresh();
    wasGeneratingOnServer.current = generatingOnServer;
  }, [generatingOnServer, refresh]);

  async function handleUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !courseId) return;
    setError(null);
    setUploading(true);
    try {
      // Request trả về ngay (chỉ lưu file + enqueue job) -- xử lý thật
      // (extract/chunk/embed, và với audio/video là phiên âm qua Gemini) chạy
      // nền qua job queue. Refresh 1 lần để thấy tài liệu mới ở trạng thái
      // uploaded; từ đó effect poll ở trên theo dõi tới khi xong.
      await uploadDocument(courseId, file, kind);
      await refresh();
    } catch (err) {
      setError(apiErrorMessage(err, "Tải tài liệu thất bại"));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleGenerate(doc: DocumentInfo) {
    if (isGenerating(doc)) return;
    const documentId = doc.id;
    setStartingIds((ids) => new Set(ids).add(documentId));
    setError(null);
    try {
      const { job_id } = await generateModule(documentId, modelAlias);
      const finalStatus = await pollJobStatus(() => getModuleJobStatus(job_id));
      if (finalStatus.status === "failed") {
        setError(finalStatus.last_error ?? "Sinh mô-đun thất bại");
      }
      await refresh();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        setError("Tài liệu này đang có 1 lượt sinh mô-đun khác đang chạy, vui lòng chờ.");
      } else {
        setError(apiErrorMessage(err, "Sinh mô-đun thất bại"));
      }
    } finally {
      setStartingIds((ids) => {
        const next = new Set(ids);
        next.delete(documentId);
        return next;
      });
    }
  }

  async function handleDeleteDocument(doc: DocumentInfo) {
    const confirmed = window.confirm(
      `Xóa vĩnh viễn tài liệu "${doc.filename}"? Các mô-đun/quiz đã sinh từ tài liệu này sẽ bị xóa theo, không thể khôi phục.`,
    );
    if (!confirmed) return;

    setDeletingDocId(doc.id);
    try {
      await deleteDocument(doc.id);
      await refresh();
    } finally {
      setDeletingDocId(null);
    }
  }

  return (
    <AppShell title="Tài liệu môn học">
      {course && <MoodleLinkSection course={course} onLinked={refresh} />}
      <div className="flex flex-wrap items-center gap-4">
        <label className="inline-flex items-center gap-2 text-sm text-gray-600">
          Loại tài liệu
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as DocumentKind)}
            disabled={loading || uploadBusy}
            className="h-10 rounded-[3px] border border-gray-300 bg-white px-2 text-sm disabled:opacity-60"
          >
            <option value="lecture">Bài giảng / slide</option>
            <option value="textbook">Giáo trình</option>
          </select>
        </label>
        <label
          className={`inline-flex items-center gap-2 rounded-[3px] bg-terracotta px-5 py-2.5 text-sm font-semibold text-white ${
            loading || uploadBusy
              ? "cursor-not-allowed opacity-60"
              : "cursor-pointer hover:bg-terracotta-dark"
          }`}
        >
          {uploadBusy ? "Đang xử lý tài liệu..." : "Tải tài liệu lên"}
          <input
            type="file"
            className="hidden"
            onChange={handleUpload}
            disabled={loading || uploadBusy}
            accept=".pdf,.pptx,.docx,.html,.htm,.txt,.vtt,.srt,.mp3,.wav,.m4a,.flac,.ogg,.aac,.mp4,.mkv,.webm,.mov,.avi"
          />
        </label>
      </div>
      {/* Hàng riêng chứ không chung hàng với nút tải lên: đây là thiết lập cho các nút
          "+ Sinh mô-đun" bên dưới, không liên quan tới việc tải tài liệu. Chung hàng thì
          dòng ghi chú dưới ô chọn còn đẩy ô chọn lệch lên so với các nút bên cạnh. */}
      <div className="mt-4">
        <ModelPicker value={modelAlias} onChange={setModelAlias} />
      </div>
      {uploadBusy && (
        <p className="mt-2 text-xs text-gray-500">
          Xử lý chạy nền (đọc tài liệu → chia đoạn → embedding; audio/video
          phiên âm qua Gemini trước) — có thể mất một lúc, đặc biệt với
          audio/video dài.
        </p>
      )}
      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}

      {loading ? (
        <p className="mt-8 text-sm text-gray-500">Đang tải...</p>
      ) : (
        <ul className="mt-8 flex flex-col divide-y divide-gray-200">
          {documents.map((d) => (
            <li key={d.id} className="py-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="font-medium">{d.filename}</div>
                  <div
                    className={`mt-0.5 text-xs font-medium ${STATUS_COLOR[d.status] ?? "text-gray-500"}`}
                  >
                    {STATUS_LABEL[d.status] ?? d.status}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-4">
                  {d.status === "embedded" && hasOutline(d) && (
                    <button
                      onClick={() => toggleOutline(d.id)}
                      className="text-sm font-semibold text-terracotta hover:underline"
                    >
                      {openOutlines.has(d.id) ? "Ẩn mục lục" : `Mục lục (${d.section_count})`}
                    </button>
                  )}
                  {d.status === "embedded" && !hasOutline(d) && (
                    <button
                      onClick={() => handleGenerate(d)}
                      disabled={isGenerating(d)}
                      className="text-sm font-semibold text-terracotta hover:underline disabled:opacity-60"
                    >
                      {isGenerating(d)
                        ? "Đang sinh mô-đun..."
                        : "+ Sinh mô-đun"}
                    </button>
                  )}
                  <button
                    onClick={() => handleDeleteDocument(d)}
                    disabled={deletingDocId === d.id}
                    className="text-sm text-red-700 hover:underline disabled:opacity-60"
                  >
                    {deletingDocId === d.id ? "Đang xóa..." : "Xóa"}
                  </button>
                </div>
              </div>
              {d.kind === "textbook" && d.status === "embedded" && d.section_count === 0 && (
                <p className="mt-2 text-xs text-gray-500">
                  Không nhận diện được mục lục của tài liệu này, nên sinh mô-đun từ toàn bộ tài liệu.
                </p>
              )}
              {openOutlines.has(d.id) && (
                <SectionTree
                  documentId={d.id}
                  documentGenerating={d.generating}
                  modules={modulesByDoc[d.id] ?? []}
                  onGenerated={refresh}
                  modelAlias={modelAlias}
                />
              )}
              {listedModules(d).length > 0 && (
                <ul className="mt-3 flex flex-col gap-1.5 pl-1">
                  {listedModules(d).map((m) => (
                    <li key={m.id}>
                      <Link
                        to={`/modules/${m.id}`}
                        className="text-sm hover:text-terracotta"
                      >
                        → {m.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
          {documents.length === 0 && (
            <p className="py-4 text-sm text-gray-500">Chưa có tài liệu nào.</p>
          )}
        </ul>
      )}
    </AppShell>
  );
}

function MoodleLinkSection({ course, onLinked }: { course: Course; onLinked: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MoodleCourseSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleUnlink() {
    // Chỉ xóa liên kết trong app -- KHÔNG đụng gì tới course/page/quiz thật
    // trên Moodle, nhưng vẫn cần xác nhận vì đây là hành động khó hoàn tác
    // trong luồng làm việc của giáo viên (phải tìm/liên kết lại từ đầu).
    const confirmed = window.confirm(
      `Hủy liên kết với Moodle course id ${course.moodle_course_id}? Course/nội dung trên Moodle sẽ KHÔNG bị xóa, chỉ mất liên kết trong app này.`,
    );
    if (!confirmed) return;

    setBusy(true);
    setError(null);
    try {
      await unlinkMoodleCourse(course.id);
      onLinked();
    } catch {
      setError("Hủy liên kết thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleSearch() {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      setResults(await searchMoodleCourses(query.trim()));
    } catch {
      setError("Tìm kiếm course trên Moodle thất bại");
    } finally {
      setSearching(false);
    }
  }

  async function handleLink(moodleCourseId: number) {
    setBusy(true);
    setError(null);
    try {
      await linkMoodleCourse(course.id, moodleCourseId);
      onLinked();
    } catch (err) {
      setError(apiErrorMessage(err, "Liên kết course thất bại"));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateNew() {
    setBusy(true);
    setError(null);
    try {
      await createMoodleCourseFor(course.id);
      onLinked();
    } catch {
      setError("Tạo course Moodle mới thất bại");
    } finally {
      setBusy(false);
    }
  }

  if (course.moodle_course_id !== null) {
    return (
      <div className="mb-6">
        <div className="flex items-center justify-between text-sm text-gray-600">
          <p>
            Đã liên kết với Moodle course id <strong>{course.moodle_course_id}</strong>.
          </p>
          <button
            onClick={handleUnlink}
            disabled={busy}
            className="font-semibold text-red-700 hover:underline disabled:opacity-60"
          >
            {busy ? "Đang hủy..." : "Hủy liên kết"}
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mb-6 rounded-[3px] border border-gray-200 p-4">
      <p className="mb-2 text-sm font-medium">Course này chưa liên kết với Moodle.</p>
      {/* Trên điện thoại ô tìm kiếm chiếm trọn một hàng và hai nút xuống hàng dưới: ép cả ba vào
          một hàng 390px thì nút "Tạo course Moodle mới" gãy thành bốn dòng. */}
      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="Tìm course Moodle có sẵn (vd: CO3061)"
          className="min-w-0 flex-1 basis-full rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm sm:basis-auto"
        />
        <button
          onClick={handleSearch}
          disabled={searching}
          className="whitespace-nowrap rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-60"
        >
          {searching ? "Đang tìm..." : "Tìm"}
        </button>
        <button
          onClick={handleCreateNew}
          disabled={busy}
          className="whitespace-nowrap rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
        >
          Tạo course Moodle mới
        </button>
      </div>
      {results.length > 0 && (
        <ul className="mt-3 flex flex-col divide-y divide-gray-100">
          {results.map((r) => (
            <li key={r.id} className="flex items-center justify-between py-2">
              <span className="text-sm">
                {r.fullname} <span className="text-gray-500">({r.shortname})</span>
              </span>
              <button
                onClick={() => handleLink(r.id)}
                disabled={busy}
                className="text-sm font-semibold text-terracotta hover:underline disabled:opacity-60"
              >
                Liên kết
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
