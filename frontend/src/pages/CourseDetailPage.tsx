import axios from "axios";
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { useParams } from "react-router-dom";
import {
  apiErrorMessage,
  createMoodleCourseFor,
  deleteDocument,
  deleteModule,
  generateModule,
  getModuleJobStatus,
  linkMoodleCourse,
  listCourses,
  listDocuments,
  listModules,
  pollJobStatus,
  retryJob,
  listMoodleCourses,
  getMoodleAccount,
  linkMoodleAccount,
  MOODLE_ACCOUNT_LINKED_EVENT,
  unlinkMoodleCourse,
  uploadDocument,
  type Course,
  type DocumentFailure,
  type DocumentInfo,
  type ModuleSummary,
  type MoodleCourse,
  type MoodleAccountStatus,
} from "../api";
import { AppShell } from "../components/AppShell";
import { ModelPicker } from "../components/ModelPicker";
import { GenerateDialog } from "../components/GenerateDialog";
import { ModuleListItem } from "../components/ModuleListItem";
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

// The list can be narrowed to what needs attention. "Lỗi" also catches a ready document whose
// module generation failed: that failure is on its document, not in its status.
type StatusFilter = "all" | "processing" | "ready" | "failed";

const FILTER_LABEL: Record<StatusFilter, string> = {
  all: "Tất cả",
  processing: "Đang xử lý",
  ready: "Sẵn sàng",
  failed: "Lỗi",
};

function matchesFilter(doc: DocumentInfo, filter: StatusFilter): boolean {
  switch (filter) {
    case "processing":
      return doc.status === "uploaded" || doc.status === "processing" || doc.status === "chunked";
    case "ready":
      return doc.status === "embedded";
    case "failed":
      return doc.status === "failed" || doc.failures.length > 0;
    default:
      return true;
  }
}

function failureTitle(failure: DocumentFailure): string {
  if (failure.job_type === "document_pipeline") return "Xử lý tài liệu thất bại";
  return failure.section_title ? `Sinh mô-đun cho mục "${failure.section_title}" thất bại` : "Sinh mô-đun thất bại";
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
  const generateDialog = useRef<HTMLDialogElement>(null);
  const generateDocument = useRef<DocumentInfo | null>(null);
  const [modulesByDoc, setModulesByDoc] = useState<
    Record<string, ModuleSummary[]>
  >({});
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);
  const [deletingModuleId, setDeletingModuleId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // What went right but is worth saying (a delete that succeeded although the list could not
  // be reloaded afterwards); shown apart from `error` so it does not read as a failure.
  const [notice, setNotice] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [retryingJobIds, setRetryingJobIds] = useState<Set<string>>(new Set());
  // Only bridges the moment between clicking and the server reporting the
  // job (so a double-click cannot send two requests); after that, and after
  // a refresh or navigation, `doc.generating` from the server is the truth.
  const [startingIds, setStartingIds] = useState<Set<string>>(new Set());
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
  const visibleDocuments = documents.filter((d) => matchesFilter(d, statusFilter));
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
      await uploadDocument(courseId, file);
      await refresh();
    } catch (err) {
      setError(apiErrorMessage(err, "Tải tài liệu thất bại"));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleGenerate(doc: DocumentInfo, outputLanguage: "vi" | "en", customInstructions: string) {
    if (isGenerating(doc)) return;
    const documentId = doc.id;
    setStartingIds((ids) => new Set(ids).add(documentId));
    setError(null);
    try {
      const { job_id } = await generateModule(documentId, modelAlias, outputLanguage, customInstructions);
      // A failed job needs no banner here: the refresh brings it back on the document, with its
      // error and a retry button, and it is still there after a reload.
      await pollJobStatus(() => getModuleJobStatus(job_id));
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

  async function handleRetry(failure: DocumentFailure) {
    setRetryingJobIds((ids) => new Set(ids).add(failure.job_id));
    setError(null);
    setNotice(null);
    try {
      await retryJob(failure.job_id);
      await refresh();
    } catch (err) {
      setError(apiErrorMessage(err, "Thử lại thất bại"));
    } finally {
      setRetryingJobIds((ids) => {
        const next = new Set(ids);
        next.delete(failure.job_id);
        return next;
      });
    }
  }

  async function handleDeleteModule(m: ModuleSummary) {
    const confirmed = window.confirm(
      `Xóa vĩnh viễn mô-đun "${m.title}"? ${
        m.is_control ? "" : "Bản đối chứng (không RAG) đi cặp với nó, nếu có, sẽ bị xóa theo. "
      }Nội dung đã đẩy lên Moodle KHÔNG bị xóa. Không thể khôi phục.`,
    );
    if (!confirmed) return;

    setDeletingModuleId(m.id);
    setError(null);
    setNotice(null);
    try {
      await deleteModule(m.id);
    } catch (err) {
      setError(apiErrorMessage(err, "Xóa mô-đun thất bại"));
      setDeletingModuleId(null);
      return;
    }
    // The module is gone whatever happens next: a list that cannot be reloaded is a different
    // problem, and saying the delete failed would send the lecturer to delete it again.
    setNotice(`Đã xóa mô-đun "${m.title}".`);
    try {
      await refresh();
    } catch (err) {
      setError(
        `${apiErrorMessage(err, "Không tải lại được danh sách")}. Mô-đun đã được xóa, hãy tải lại trang để thấy danh sách mới.`,
      );
    } finally {
      setDeletingModuleId(null);
    }
  }

  return (
    <AppShell title="Tài liệu môn học">
      <GenerateDialog dialogRef={generateDialog} onGenerate={(language, instructions) => {
        if (generateDocument.current) void handleGenerate(generateDocument.current, language, instructions);
      }} />
      {course && <MoodleLinkSection course={course} onLinked={refresh} />}
      <div className="flex flex-wrap items-center gap-4">
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
          phiên âm qua Whisper trước) — có thể mất một lúc, đặc biệt với
          audio/video dài.
        </p>
      )}
      {notice && <p className="mt-3 text-sm text-green-800">{notice}</p>}
      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}

      {!loading && documents.length > 0 && (
        <div className="mt-6 flex items-center gap-2">
          <label htmlFor="document-status-filter" className="text-sm text-gray-600">
            Trạng thái:
          </label>
          <select
            id="document-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="rounded-[3px] border border-gray-300 px-2 py-1 text-sm"
          >
            {(Object.keys(FILTER_LABEL) as StatusFilter[]).map((value) => (
              <option key={value} value={value}>
                {FILTER_LABEL[value]}
              </option>
            ))}
          </select>
        </div>
      )}

      {loading ? (
        <p className="mt-8 text-sm text-gray-500">Đang tải...</p>
      ) : (
        <ul className="mt-4 flex flex-col divide-y divide-gray-200">
          {visibleDocuments.map((d) => (
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
                      onClick={() => { generateDocument.current = d; generateDialog.current?.showModal(); }}
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
              {d.failures.length > 0 && (
                <ul className="mt-2 flex flex-col gap-2">
                  {d.failures.map((f) => (
                    <li
                      key={f.job_id}
                      className="flex flex-wrap items-start justify-between gap-3 rounded-[3px] border border-red-200 bg-red-50 px-3 py-2"
                    >
                      <div className="min-w-0 text-sm text-red-800">
                        <div className="font-medium">{failureTitle(f)}</div>
                        {f.error && <div className="mt-0.5 break-words text-xs">{f.error}</div>}
                      </div>
                      <button
                        onClick={() => handleRetry(f)}
                        disabled={retryingJobIds.has(f.job_id)}
                        aria-label={`Thử lại: ${failureTitle(f)} (${d.filename})`}
                        className="shrink-0 text-sm font-semibold text-terracotta hover:underline disabled:opacity-60"
                      >
                        {retryingJobIds.has(f.job_id) ? "Đang thử lại..." : "Thử lại"}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
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
                  onDeleteModule={handleDeleteModule}
                  deletingModuleId={deletingModuleId}
                  modelAlias={modelAlias}
                />
              )}
              {listedModules(d).length > 0 && (
                <ul className="mt-3 flex flex-col gap-1.5 pl-1">
                  {listedModules(d).map((m) => (
                    <ModuleListItem
                      key={m.id}
                      module={m}
                      deleting={deletingModuleId === m.id}
                      onDelete={handleDeleteModule}
                    />
                  ))}
                </ul>
              )}
            </li>
          ))}
          {documents.length === 0 && (
            <p className="py-4 text-sm text-gray-500">Chưa có tài liệu nào.</p>
          )}
          {documents.length > 0 && visibleDocuments.length === 0 && (
            <p className="py-4 text-sm text-gray-500">Không có tài liệu nào ở trạng thái này.</p>
          )}
        </ul>
      )}
    </AppShell>
  );
}

function MoodleLinkSection({ course, onLinked }: { course: Course; onLinked: () => void }) {
  // "unknown" until the server answers; a failed or missing answer leaves it there, and the
  // server refuses a link without the account anyway.
  const [account, setAccount] = useState<MoodleAccountStatus | "unknown">("unknown");
  const [courses, setCourses] = useState<MoodleCourse[]>([]);
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const linked = course.moodle_course_id !== null;

  // The Moodle account first, then the courses to pick from. Sets state only from promise
  // callbacks, and drops an answer that arrives after the effect was cleaned up.
  useEffect(() => {
    if (linked) return;
    let cancelled = false;
    const onAccountLinked = () => setReloads((n) => n + 1);
    window.addEventListener(MOODLE_ACCOUNT_LINKED_EVENT, onAccountLinked);
    Promise.resolve(getMoodleAccount())
      .then((answer) => {
        const status = answer?.status ?? "unknown";
        if (!cancelled) setAccount(status);
        return status === "not_linked" || status === "ambiguous" ? undefined : listMoodleCourses();
      })
      .then(
        (list) => {
          if (!cancelled && list) {
            setCourses(list);
            setError(null);
          }
        },
        (err) => {
          if (!cancelled) setError(apiErrorMessage(err, "Không tải được danh sách course Moodle"));
        },
      );
    return () => {
      cancelled = true;
      window.removeEventListener(MOODLE_ACCOUNT_LINKED_EVENT, onAccountLinked);
    };
  }, [linked, reloads]);

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

  async function handleLinkAccount() {
    setBusy(true);
    setError(null);
    try {
      await linkMoodleAccount();
      setAccount("linked");
    } catch (err) {
      setError(apiErrorMessage(err, "Liên kết tài khoản Moodle thất bại"));
    } finally {
      setBusy(false);
    }
  }

  async function handleLink() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      await linkMoodleCourse(course.id, Number(chosen));
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
    } catch (err) {
      // The server explains a refusal such as "no Moodle account has your email".
      setError(apiErrorMessage(err, "Tạo course Moodle mới thất bại"));
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

  if (account === "not_linked" || account === "ambiguous") {
    return (
      <div className="mb-6 rounded-[3px] border border-gray-200 p-4">
        <p className="mb-2 text-sm font-medium">Course này chưa liên kết với Moodle.</p>
        <p className="mb-3 text-sm text-gray-600">
          {account === "not_linked"
            ? "Bạn cần liên kết tài khoản Moodle của mình trước: tài khoản Moodle có cùng email sẽ được liên kết, nếu chưa có thì hệ thống tạo mới."
            : "Có nhiều tài khoản Moodle cùng email của bạn nên không xác định được tài khoản để liên kết. Hãy nhờ admin Moodle xử lý trùng email."}
        </p>
        {account === "not_linked" && (
          <button
            onClick={handleLinkAccount}
            disabled={busy}
            className="whitespace-nowrap rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
          >
            {busy ? "Đang liên kết..." : "Liên kết tài khoản Moodle"}
          </button>
        )}
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mb-6 rounded-[3px] border border-gray-200 p-4">
      <p className="mb-2 text-sm font-medium">Course này chưa liên kết với Moodle.</p>
      {/* Trên điện thoại ô chọn chiếm trọn một hàng và hai nút xuống hàng dưới: ép cả ba vào
          một hàng 390px thì nút "Tạo course Moodle mới" gãy thành bốn dòng. */}
      <div className="flex flex-wrap gap-2">
        <select
          aria-label="Course Moodle có sẵn"
          value={chosen}
          onChange={(e) => setChosen(e.target.value)}
          className="min-w-0 flex-1 basis-full rounded-[3px] border border-gray-300 bg-white px-3 py-1.5 text-sm sm:basis-auto"
        >
          <option value="">Chọn course Moodle có sẵn…</option>
          {courses.map((c) => (
            <option key={c.id} value={c.id}>
              {c.fullname} ({c.shortname})
            </option>
          ))}
        </select>
        <button
          onClick={handleLink}
          disabled={busy || !chosen}
          className="whitespace-nowrap rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-60"
        >
          Liên kết
        </button>
        <button
          onClick={handleCreateNew}
          disabled={busy}
          className="whitespace-nowrap rounded-[3px] bg-navy px-3 py-1.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
        >
          Tạo course Moodle mới
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
