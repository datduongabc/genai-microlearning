import axios from "axios";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useParams } from "react-router-dom";
import {
  apiErrorMessage,
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
  unlinkMoodleCourse,
  uploadDocument,
  type Course,
  type DocumentFailure,
  type DocumentInfo,
  type ModuleSummary,
  type MoodleCourse,
} from "../api";
import { AppShell } from "../components/AppShell";
import { CourseChat } from "../components/CourseChat";
import { ModelPicker } from "../components/ModelPicker";
import { MoodleAccountSection, type MoodleAccountState } from "../components/MoodleAccountSection";
import { GenerateDialog } from "../components/GenerateDialog";
import {
  linkMoodleCourseSchema,
  uploadDocumentSchema,
  type LinkMoodleCourseFormValues,
  type UploadDocumentFormValues,
} from "../lib/formSchemas";
import { ModuleListItem } from "../components/ModuleListItem";

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
  return "Sinh mô-đun thất bại";
}

const WARNING_LABEL: Record<string, string> = {
  ocr_failed: "OCR không đọc được",
  formula_unverified: "Công thức có thể đọc chưa đúng ở",
};

const DEFAULT_WARNING_ADVICE = "Nội dung có thể thiếu; hãy kiểm tra hoặc tải lại bản PDF có lớp chữ.";
const WARNING_ADVICE: Record<string, string> = {
  formula_unverified: "Hãy soát lại công thức của các trang này trước khi dùng nội dung sinh ra.",
};

function extractionWarningLines(warnings: DocumentInfo["extraction_warnings"]) {
  const pagesByCode = new Map<string, number[]>();
  for (const w of warnings) pagesByCode.set(w.code, [...(pagesByCode.get(w.code) ?? []), w.page]);
  return [...pagesByCode].map(([code, pages]) => ({
    code,
    text: `${WARNING_LABEL[code] ?? code} ${pages.length > 1 ? "các trang" : "trang"} ${pages.sort((a, b) => a - b).join(", ")}. `
      + (WARNING_ADVICE[code] ?? DEFAULT_WARNING_ADVICE),
  }));
}

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
  // Model cho các lượt sinh bấm từ trang này, dùng chung cho tài liệu;
  // chuỗi rỗng nghĩa là chưa chọn gì. ModelPicker điền sẵn mặc định đã lưu của giảng viên.
  const [modelAlias, setModelAlias] = useState("");
  const generateDialog = useRef<HTMLDialogElement>(null);
  const generateDocument = useRef<DocumentInfo | null>(null);
  const [modulesByDoc, setModulesByDoc] = useState<
    Record<string, ModuleSummary[]>
  >({});
  const [loading, setLoading] = useState(true);
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
  const upload = useForm<UploadDocumentFormValues>({ resolver: zodResolver(uploadDocumentSchema) });
  const uploading = upload.formState.isSubmitting;
  // Share of the file sent, while it is being sent; null otherwise.
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const uploadBusy = uploading || pipelineActive;
  const generatingOnServer = documents.some((d) => d.generating);
  const visibleDocuments = documents.filter((d) => matchesFilter(d, statusFilter));
  const shouldPoll = pipelineActive || generatingOnServer;
  const isGenerating = (doc: DocumentInfo) => doc.generating || startingIds.has(doc.id);
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

  async function handleUpload({ file }: UploadDocumentFormValues) {
    if (!courseId) return;
    setError(null);
    try {
      // Ends once the file is stored and the job queued; the processing itself (extract/chunk/
      // embed, and for audio/video the Whisper transcription first) runs in the background.
      // One refresh shows the new document as uploaded; the polling above follows it from there.
      setUploadProgress(0);
      await uploadDocument(courseId, file, setUploadProgress);
      await refresh();
    } catch (err) {
      setError(apiErrorMessage(err, "Tải tài liệu thất bại"));
    } finally {
      setUploadProgress(null);
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
        setError("Tài liệu này đang sinh một mô-đun khác. Chờ lượt đó xong rồi thử lại.");
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
    setError(null);
    setNotice(null);
    try {
      await deleteDocument(doc.id);
    } catch (err) {
      setError(apiErrorMessage(err, "Xóa tài liệu thất bại"));
      setDeletingDocId(null);
      return;
    }
    setNotice(`Đã xóa tài liệu "${doc.filename}".`);
    try {
      await refresh();
    } catch (err) {
      setError(
        `${apiErrorMessage(err, "Không tải lại được danh sách")}. Tài liệu đã được xóa, hãy tải lại trang để thấy danh sách mới.`,
      );
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
    } catch (err) {
      setError(apiErrorMessage(err, "Thử lại thất bại"));
      setRetryingJobIds((ids) => {
        const next = new Set(ids);
        next.delete(failure.job_id);
        return next;
      });
      return;
    }
    // The job is queued again whatever happens next: retrying it once more would be refused.
    setNotice("Đã gửi yêu cầu thử lại.");
    try {
      await refresh();
    } catch (err) {
      setError(
        `${apiErrorMessage(err, "Không tải lại được danh sách")}. Đã gửi yêu cầu thử lại, hãy tải lại trang để thấy trạng thái mới.`,
      );
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
    <AppShell title={course?.name ?? "Tài liệu môn học"}>
      <GenerateDialog dialogRef={generateDialog} onGenerate={(language, instructions) => {
        if (generateDocument.current) void handleGenerate(generateDocument.current, language, instructions);
      }} />
      {course && <MoodleLinkSection course={course} onLinked={refresh} />}
      <div className="course-layout">
      <section className="course-documents" aria-labelledby="course-documents-title">
      <h2 id="course-documents-title" className="course-section-title">Tài liệu</h2>
      <div className="course-toolbar">
        <label
          className={`ui-button primary ${
            loading || uploadBusy ? "cursor-not-allowed opacity-60" : "cursor-pointer"
          }`}
        >
          {uploadProgress !== null
            ? `Đang tải lên ${Math.round(uploadProgress * 100)}%`
            : uploadBusy
              ? "Đang xử lý tài liệu..."
              : "Tải tài liệu lên"}
          <input
            type="file"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              upload.setValue("file", file);
              void upload.handleSubmit(handleUpload, (errors) => setError(errors.file?.message ?? null))();
            }}
            disabled={loading || uploadBusy}
            accept=".pdf,.pptx,.docx,.html,.htm,.mp3,.wav,.m4a,.flac,.ogg,.aac,.mp4,.mkv,.webm,.mov,.avi"
          />
        </label>
        {/* Thiết lập cho các nút "Sinh mô-đun" bên dưới, không liên quan tới việc tải tài liệu,
            nhưng đặt cạnh nút tải để hai thiết lập của cột nằm trên một hàng. */}
        <ModelPicker value={modelAlias} onChange={setModelAlias} />
      </div>
      <p className="course-hint">
        PDF, PPTX (đến 150 trang), DOCX, HTML: tối đa 25 MB. Audio tối đa 200 MB, video tối đa 1 GB, dài tối đa 90 phút.
      </p>
      {uploadBusy && (
        <p className="course-hint">
          Đang đọc và lập chỉ mục tài liệu. Audio, video dài có thể mất vài phút.
        </p>
      )}
      {notice && <p role="status" className="mt-3 text-sm success-text">{notice}</p>}
      {error && <p className="mt-3 text-sm danger-text">{error}</p>}

      {!loading && documents.length > 0 && (
        <div className="course-filter">
          <label htmlFor="document-status-filter">Trạng thái:</label>
          <select
            id="document-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="ui-select"
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
        <p className="course-empty">Đang tải tài liệu...</p>
      ) : (
        <ul className="doc-list">
          {visibleDocuments.map((d) => (
            <li key={d.id} className="doc-row">
              <div className="doc-head">
                <div className="min-w-0">
                  <div className="doc-name">{d.filename}</div>
                  <div className="status-pill" data-status={d.status}>
                    {STATUS_LABEL[d.status] ?? d.status}
                  </div>
                </div>
                <div className="doc-actions">
                  {d.status === "embedded" && (
                    <button
                      onClick={() => { generateDocument.current = d; generateDialog.current?.showModal(); }}
                      disabled={isGenerating(d)}
                      className="link-action"
                    >
                      {isGenerating(d)
                        ? "Đang sinh mô-đun..."
                        : "+ Sinh mô-đun"}
                    </button>
                  )}
                  <button
                    onClick={() => handleDeleteDocument(d)}
                    disabled={deletingDocId === d.id}
                    className="link-action danger"
                  >
                    {deletingDocId === d.id ? "Đang xóa..." : "Xóa"}
                  </button>
                </div>
              </div>
              {extractionWarningLines(d.extraction_warnings).map((warning) => (
                <p key={warning.code} className="doc-warning">
                  {warning.text}
                </p>
              ))}
              {d.failures.length > 0 && (
                <ul className="mt-3 flex flex-col gap-2">
                  {d.failures.map((f) => (
                    <li key={f.job_id} className="doc-failure">
                      <div className="min-w-0">
                        <div className="font-semibold">{failureTitle(f)}</div>
                        {f.error && <div className="mt-0.5 break-words text-[13px] leading-[19px]">{f.error}</div>}
                      </div>
                      <button
                        onClick={() => handleRetry(f)}
                        disabled={retryingJobIds.has(f.job_id)}
                        aria-label={`Thử lại: ${failureTitle(f)} (${d.filename})`}
                        className="link-action shrink-0"
                      >
                        {retryingJobIds.has(f.job_id) ? "Đang thử lại..." : "Thử lại"}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {(modulesByDoc[d.id] ?? []).length > 0 && (
                <ul className="doc-modules" aria-label={`Mô-đun sinh từ ${d.filename}`}>
                  {(modulesByDoc[d.id] ?? []).map((m) => (
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
            <p className="course-empty">Chưa có tài liệu. Tải tài liệu lên để bắt đầu.</p>
          )}
          {documents.length > 0 && visibleDocuments.length === 0 && (
            <p className="course-empty">Không có tài liệu nào ở trạng thái này.</p>
          )}
        </ul>
      )}
      </section>
      <div className="course-chat">
        {courseId && documents.some((d) => d.status === "embedded")
          ? <CourseChat key={courseId} courseId={courseId} />
          : !loading && (
            <section className="chat-panel" aria-label="Hỏi đáp về tài liệu">
              <h2 className="course-section-title">Hỏi đáp về tài liệu</h2>
              <p className="course-empty">Dùng được khi có một tài liệu xử lý xong.</p>
            </section>
          )}
      </div>
      </div>
    </AppShell>
  );
}

function MoodleLinkSection({ course, onLinked }: { course: Course; onLinked: () => void }) {
  // What the "Tài khoản Moodle" section above got from the server; "unknown" until it answers,
  // and the server refuses a course link without the account anyway.
  const [account, setAccount] = useState<MoodleAccountState>("unknown");
  const [courses, setCourses] = useState<MoodleCourse[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const linked = course.moodle_course_id !== null;
  const { register, handleSubmit, control } = useForm<LinkMoodleCourseFormValues>({
    resolver: zodResolver(linkMoodleCourseSchema),
    defaultValues: { moodleCourseId: "" },
  });
  const chosen = useWatch({ control, name: "moodleCourseId" });

  // The courses to pick from, once the account is linked. Sets state only from promise
  // callbacks, and drops an answer that arrives after the effect was cleaned up.
  useEffect(() => {
    if (linked || account !== "linked") return;
    let cancelled = false;
    listMoodleCourses().then(
      (list) => {
        if (!cancelled) {
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
    };
  }, [linked, account]);

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

  async function handleLink({ moodleCourseId }: LinkMoodleCourseFormValues) {
    setBusy(true);
    setError(null);
    try {
      await linkMoodleCourse(course.id, Number(moodleCourseId));
      onLinked();
    } catch (err) {
      setError(apiErrorMessage(err, "Liên kết course thất bại"));
    } finally {
      setBusy(false);
    }
  }

  const accountSection = <MoodleAccountSection onStatus={setAccount} />;

  const panel = (row: ReactNode) => (
    <div className="moodle-panel">
      {accountSection}
      <div className="moodle-course">{row}</div>
      {error && <p className="moodle-error danger-text">{error}</p>}
    </div>
  );

  if (course.moodle_course_id !== null) {
    return panel(
      <>
        <p>
          <span className="moodle-key">Khóa học Moodle:</span>{" "}
          Đã liên kết với Moodle course id <strong>{course.moodle_course_id}</strong>.
        </p>
        <button onClick={handleUnlink} disabled={busy} className="link-action danger">
          {busy ? "Đang hủy..." : "Hủy liên kết"}
        </button>
      </>,
    );
  }

  // One form whose first line never changes, whatever the account answers: the line used to be
  // a different element before and after the answer, so it flashed "link your account first"
  // to a lecturer whose account was linked.
  return panel(
    <form
      onSubmit={handleSubmit(handleLink, (errors) => setError(errors.moodleCourseId?.message ?? null))}
      noValidate
      className="moodle-link-form"
    >
      <span className="moodle-key">Môn học này chưa liên kết với Moodle.</span>
      {/* Where Moodle is unavailable the account section above already says so. */}
      {account !== "unknown" && account !== "unavailable" && account !== "linked" && (
        <span>Liên kết tài khoản Moodle trước để chọn khóa học.</span>
      )}
      {account === "linked" && (
        <>
          <span>Chọn khóa học:</span>
          <select
            aria-label="Course Moodle có sẵn"
            {...register("moodleCourseId")}
            className="ui-select min-w-0 flex-1 basis-56"
          >
            <option value="">Chọn khóa học bạn có quyền quản lý hoạt động…</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>
                {c.fullname} ({c.shortname})
              </option>
            ))}
          </select>
          <button type="submit" disabled={busy || !chosen} className="ui-button">
            Liên kết
          </button>
        </>
      )}
    </form>,
  );
}
