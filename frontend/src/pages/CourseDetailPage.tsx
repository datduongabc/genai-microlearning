import { confirmAction } from "../lib/confirm";
import { notifyDone, notifyFailed } from "../lib/notify";
import { BookOpenText, ChevronDown, Clapperboard, Ellipsis, Layers, Plus, Trash2 } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../components/ui/dropdown-menu";
import axios from "axios";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useParams } from "react-router-dom";
import {
  apiErrorMessage,
  deleteDocument,
  deleteFlashcardSet,
  deleteModule,
  deleteVideo,
  generateFlashcards,
  generateModule,
  generateVideo,
  getModuleJobStatus,
  getMoodleAccount,
  MOODLE_ACCOUNT_CHANGED_EVENT,
  linkMoodleCourse,
  listCourses,
  listDocuments,
  listFlashcardSets,
  listModules,
  listVideos,
  pollJobStatus,
  retryJob,
  listMoodleCourses,
  uploadDocument,
  type Course,
  type DocumentFailure,
  type DocumentInfo,
  type FlashcardSetSummary,
  type ModuleSummary,
  type MoodleCourse,
  type VideoSummary,
} from "../api";
import { AppShell } from "../components/AppShell";
import { CourseChat } from "../components/CourseChat";
import type { MoodleAccountState } from "../components/MoodleAccountSection";
import { GenerateDialog, type GenerateKind, type GenerateChoice } from "../components/GenerateDialog";
import { focusReturnTarget } from "../lib/focusReturn";
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
  chunked: "Đang xử lý",
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
  if (failure.job_type === "flashcard_generation") return "Tạo flashcard thất bại";
  if (failure.job_type === "video_script") return "Viết lời thoại video thất bại";
  return "Tạo mô-đun thất bại";
}

const WARNING_LABEL: Record<string, string> = {
  ocr_failed: "Không nhận dạng được chữ ở",
  formula_unverified: "Công thức có thể đọc chưa đúng ở",
};

const DEFAULT_WARNING_ADVICE = "Nội dung có thể thiếu. Hãy kiểm tra lại, hoặc tải lên bản PDF có lớp chữ.";
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

// A row says only what is not settled: a job running or failed. A ready item, of any kind, is just its name.
function flashcardStatusText(set: FlashcardSetSummary): string | null {
  if (set.status === "generating") return "Đang tạo…";
  if (set.status === "failed") return "Tạo thất bại";
  return null;
}

// The statuses a video has while one of its jobs runs in the worker.
const VIDEO_JOB_TEXT: Partial<Record<VideoSummary["status"], string>> = {
  generating: "Đang tạo…",
  drawing: "Đang vẽ hình…",
  rendering: "Đang dựng video…",
};

function videoStatusText(video: VideoSummary): string | null {
  const running = VIDEO_JOB_TEXT[video.status];
  if (running) return running;
  // A failed picture or render leaves the scenes to edit; only a failed script leaves nothing.
  if (video.status === "failed" && video.scene_count === 0) return "Tạo thất bại";
  if (video.status === "failed") return "Có lỗi";
  return null;
}

// What a list row's pill means: "processing" while a job runs, red when it failed.
function mediaPillStatus(running: boolean, failed: boolean): string | undefined {
  if (failed) return "failed";
  if (running) return "processing";
  return undefined;
}

type MediaKind = "Flashcard" | "Video";


// When an item was made, as "tạo 00:20 16/11" in the lecturer's time: enough to tell two items of one
// focus apart, which live run 2's page could only do by their card or scene counts.
function createdText(iso: string): string {
  const at = new Date(iso);
  const two = (n: number) => String(n).padStart(2, "0");
  return `Tạo lúc ${two(at.getHours())}:${two(at.getMinutes())}, ${two(at.getDate())}/${two(at.getMonth() + 1)}`;
}

// The creation time of each item whose title another item of its document's list also has, by id.
function sharedTitleDates(items: { id: string; document_id: string; focus: string | null; created_at: string }[]) {
  const title = (item: (typeof items)[number]) => `${item.document_id}\n${item.focus ?? ""}`;
  const count = new Map<string, number>();
  items.forEach((item) => count.set(title(item), (count.get(title(item)) ?? 0) + 1));
  return new Map(items.filter((item) => (count.get(title(item)) ?? 0) > 1).map((item) => [item.id, item.created_at]));
}

// The kind is written out because a set and a video made from the same focus carry the same title.
function MediaListItem({ kind, to, focus, status, statusText, created, deleting, onDelete }: {
  kind: MediaKind; to: string; focus: string | null; status?: string; statusText: string | null; created?: string;
  deleting: boolean; onDelete: () => void;
}) {
  const name = focus ?? "Toàn bộ tài liệu";
  return (
    <li className="module-item media-item">
      <span className="media-kind">{kind}</span>
      <Link to={to} className="module-link flashcard-link">
        {name}
      </Link>
      <button onClick={onDelete} disabled={deleting} aria-label={`Xóa ${kind.toLowerCase()} ${name}`}
        className="link-action danger small-action media-delete">
        {deleting ? "Đang xóa…" : "Xóa"}
      </button>
      {(statusText || created) && (
        <span className="media-meta">
          {statusText && <span className="status-pill" data-status={status}>{statusText}</span>}
          {created && <time className="media-created" dateTime={created}>{createdText(created)}</time>}
        </span>
      )}
    </li>
  );
}

// A request the server has not answered yet. It can take seconds (live run 2: 8 s), and until it
// answered the page showed nothing, so the lecturer could not tell the click was taken.
interface PendingMedia {
  key: number;
  documentId: string;
  kind: MediaKind;
  focus: string | null;
}

function PendingMediaItem({ item }: { item: PendingMedia }) {
  return (
    <li className="module-item media-item" aria-busy="true">
      <span className="media-kind">{item.kind}</span>
      <span className="module-link">{item.focus ?? "Toàn bộ tài liệu"}</span>
      <span className="media-meta"><span className="status-pill" data-status="processing">Đang gửi yêu cầu…</span></span>
    </li>
  );
}

// A module being generated has no title yet, but it takes a row like a set or a video does.
function PendingModuleItem() {
  return (
    <li className="module-item media-item" aria-busy="true">
      <span className="media-kind">Mô-đun</span>
      <span className="module-link">Mô-đun mới</span>
      <span className="media-meta"><span className="status-pill" data-status="processing">Đang tạo…</span></span>
    </li>
  );
}

interface CoursePageSetters {
  setCourse: (course: Course | null) => void;
  setDocuments: (documents: DocumentInfo[]) => void;
  setModulesByDoc: (modules: Record<string, ModuleSummary[]>) => void;
  setFlashcardSets: (sets: FlashcardSetSummary[]) => void;
  setVideos: (videos: VideoSummary[]) => void;
}

// Hands over the documents as soon as they arrive and their modules a moment
// later, so the list appears without waiting for one request per document.
async function loadCoursePage(courseId: string, apply: CoursePageSetters) {
  const [courses, docs, sets, videos] = await Promise.all([
    listCourses(), listDocuments(courseId), listFlashcardSets(courseId), listVideos(courseId),
  ]);
  apply.setCourse(courses.find((c) => c.id === courseId) ?? null);
  apply.setDocuments(docs);
  apply.setFlashcardSets(sets);
  apply.setVideos(videos);
  const entries = await Promise.all(
    docs.map(async (d) => [d.id, await listModules(d.id)] as const),
  );
  apply.setModulesByDoc(Object.fromEntries(entries));
}

export function CourseDetailPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const [course, setCourse] = useState<Course | null>(null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  // The document a generate dialog is for and the button to give focus back to. Kept after the
  // dialog closes: the dialog hands focus back only once it has finished closing.
  const [asking, setAsking] = useState<{ kind: GenerateKind; document: DocumentInfo; returnTo: HTMLElement | null; open: boolean } | null>(null);
  const ask = (kind: GenerateKind, document: DocumentInfo) => setAsking({ kind, document, returnTo: focusReturnTarget(), open: true });
  const dialogProps = (kind: GenerateKind) => ({
    kind, open: asking?.kind === kind && asking.open, returnFocusTo: asking?.returnTo,
    onOpenChange: (open: boolean) => { if (!open) setAsking((was) => was && { ...was, open: false }); },
  });
  const [flashcardSets, setFlashcardSets] = useState<FlashcardSetSummary[]>([]);
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [deletingMediaId, setDeletingMediaId] = useState<string | null>(null);
  const [modulesByDoc, setModulesByDoc] = useState<
    Record<string, ModuleSummary[]>
  >({});
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);
  const [deletingModuleId, setDeletingModuleId] = useState<string | null>(null);
  // What went right but is worth saying (a delete that succeeded although the list could not
  // be reloaded afterwards); shown apart from `error` so it does not read as a failure.
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [retryingJobIds, setRetryingJobIds] = useState<Set<string>>(new Set());
  // Only bridges the moment between clicking and the server reporting the
  // job (so a double-click cannot send two requests); after that, and after
  // a refresh or navigation, `doc.generating` from the server is the truth.
  const [startingIds, setStartingIds] = useState<Set<string>>(new Set());
  // Flashcard and video requests on their way, one of each kind per document: a second click before
  // the first answers would only be refused as a duplicate. The ref answers at once, the state draws.
  const [pendingMedia, setPendingMedia] = useState<PendingMedia[]>([]);
  const pendingKeys = useRef(new Set<string>());
  const nextPending = useRef(0);
  const refresh = useCallback(async () => {
    if (!courseId) return;
    await loadCoursePage(courseId, { setCourse, setDocuments, setModulesByDoc, setFlashcardSets, setVideos });
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
          setFlashcardSets: guarded(setFlashcardSets),
          setVideos: guarded(setVideos),
        })
      : Promise.resolve();
    first
      // An empty list after a failed load would read "no documents yet": say it failed instead.
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [courseId, attempt]);

  function retryLoad() {
    setLoading(true);
    setLoadFailed(false);
    setAttempt((n) => n + 1);
  }

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
  const setsByDoc = (documentId: string) => flashcardSets.filter((set) => set.document_id === documentId);
  const videosByDoc = (documentId: string) => videos.filter((video) => video.document_id === documentId);
  const isGenerating = (doc: DocumentInfo) => doc.generating || startingIds.has(doc.id);
  const setDates = sharedTitleDates(flashcardSets);
  const videoDates = sharedTitleDates(videos);
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

  // A set being generated is a job in the worker: the server's status is the truth, so the list
  // is read again until none is generating, as it is for documents above.
  const flashcardsGenerating = flashcardSets.some((set) => set.status === "generating");
  useEffect(() => {
    if (!flashcardsGenerating || !courseId) return;
    const timer = setInterval(async () => {
      try {
        setFlashcardSets(await listFlashcardSets(courseId));
      } catch {
        // transient failure: the next tick retries
      }
    }, DOCUMENT_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [flashcardsGenerating, courseId]);

  // The same for videos, while any of them has a job in the worker.
  const videosRunning = videos.some((video) => VIDEO_JOB_TEXT[video.status] !== undefined);
  useEffect(() => {
    if (!videosRunning || !courseId) return;
    const timer = setInterval(async () => {
      try {
        setVideos(await listVideos(courseId));
      } catch {
        // transient failure: the next tick retries
      }
    }, DOCUMENT_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [videosRunning, courseId]);

  async function handleUpload({ file }: UploadDocumentFormValues) {
    if (!courseId) return;
    try {
      // Ends once the file is stored and the job queued; the processing itself (extract/chunk/
      // embed, and for audio/video the Whisper transcription first) runs in the background.
      // One refresh shows the new document as uploaded; the polling above follows it from there.
      setUploadProgress(0);
      await uploadDocument(courseId, file, setUploadProgress);
      await refresh();
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Tải tài liệu thất bại"));
    } finally {
      setUploadProgress(null);
    }
  }

  async function handleGenerate(doc: DocumentInfo, outputLanguage: "vi" | "en", customInstructions: string, modelAlias: string) {
    if (isGenerating(doc)) return;
    const documentId = doc.id;
    setStartingIds((ids) => new Set(ids).add(documentId));
    try {
      const { job_id } = await generateModule(documentId, modelAlias, outputLanguage, customInstructions);
      // A failed job needs no banner here: the refresh brings it back on the document, with its
      // error and a retry button, and it is still there after a reload.
      await pollJobStatus(() => getModuleJobStatus(job_id));
      await refresh();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        notifyFailed("Tài liệu này đang sinh một mô-đun khác. Chờ lượt đó xong rồi thử lại.");
      } else {
        notifyFailed(apiErrorMessage(err, "Tạo mô-đun thất bại"));
      }
    } finally {
      setStartingIds((ids) => {
        const next = new Set(ids);
        next.delete(documentId);
        return next;
      });
    }
  }

  const isSending = (doc: DocumentInfo, kind: MediaKind) => pendingMedia.some((p) => p.documentId === doc.id && p.kind === kind);
  const pendingFor = (doc: DocumentInfo, kind: MediaKind) => pendingMedia.filter((p) => p.documentId === doc.id && p.kind === kind);

  // Shows the request as a row under its document until the list it lands in has been read again.
  async function sendMediaRequest(doc: DocumentInfo, kind: MediaKind, focus: string, send: () => Promise<void>) {
    const guard = `${kind}:${doc.id}`;
    if (pendingKeys.current.has(guard)) return;
    pendingKeys.current.add(guard);
    const item = { key: nextPending.current++, documentId: doc.id, kind, focus: focus.trim() || null };
    setPendingMedia((items) => [...items, item]);
    try {
      await send();
    } finally {
      pendingKeys.current.delete(guard);
      setPendingMedia((items) => items.filter((p) => p.key !== item.key));
    }
  }

  // The paid request and the reload of the list after it fail apart: told "failed" when only the
  // reload did, a lecturer would generate again and pay twice for the same thing.
  async function startGeneration(
    generate: () => Promise<unknown>, reload: () => Promise<void>, failed: string, started: string,
  ) {
    try {
      await generate();
    } catch (err) {
      // A 409 (the same item is being generated) and a 429 (quota) carry their own Vietnamese wording.
      notifyFailed(apiErrorMessage(err, failed));
      return;
    }
    try {
      await reload();
    } catch (err) {
      // Said only then: otherwise the new row in the list says it.
      notifyDone(`${started}.`);
      notifyFailed(`${apiErrorMessage(err, "Không tải lại được danh sách")}. ${started}, hãy tải lại trang để theo dõi.`);
    }
  }

  async function handleGenerateFlashcards(doc: DocumentInfo, outputLanguage: "vi" | "en", focus: string, modelAlias: string) {
    await sendMediaRequest(doc, "Flashcard", focus, () => startGeneration(
      () => generateFlashcards(doc.id, { focus, outputLanguage, modelAlias }),
      async () => setFlashcardSets(await listFlashcardSets(courseId ?? "")),
      "Tạo flashcard thất bại", "Đã bắt đầu tạo flashcard",
    ));
  }

  async function handleGenerateVideo(doc: DocumentInfo, outputLanguage: "vi" | "en", focus: string, choice: GenerateChoice) {
    await sendMediaRequest(doc, "Video", focus, () => startGeneration(
      () => generateVideo(doc.id, { focus, outputLanguage, ...choice }),
      async () => setVideos(await listVideos(courseId ?? "")),
      "Tạo video thất bại", "Đã bắt đầu tạo video",
    ));
  }

  async function handleDeleteDocument(doc: DocumentInfo) {
    const confirmed = await confirmAction({
      title: `Xóa tài liệu "${doc.filename}"?`,
      description: "Các mô-đun, quiz, flashcard và video sinh từ tài liệu này bị xóa theo. Không thể khôi phục.",
      action: "Xóa tài liệu", destructive: true,
    });
    if (!confirmed) return;

    setDeletingDocId(doc.id);
    try {
      await deleteDocument(doc.id);
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Xóa tài liệu thất bại"));
      setDeletingDocId(null);
      return;
    }
    notifyDone(`Đã xóa tài liệu "${doc.filename}".`);
    try {
      await refresh();
    } catch (err) {
      notifyFailed(
        `${apiErrorMessage(err, "Không tải lại được danh sách")}. Tài liệu đã được xóa, hãy tải lại trang để thấy danh sách mới.`,
      );
    } finally {
      setDeletingDocId(null);
    }
  }

  async function handleRetry(failure: DocumentFailure) {
    setRetryingJobIds((ids) => new Set(ids).add(failure.job_id));
    try {
      await retryJob(failure.job_id);
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Thử lại thất bại"));
      setRetryingJobIds((ids) => {
        const next = new Set(ids);
        next.delete(failure.job_id);
        return next;
      });
      return;
    }
    // The job is queued again whatever happens next: retrying it once more would be refused.
    notifyDone("Đã gửi yêu cầu thử lại.");
    try {
      await refresh();
    } catch (err) {
      notifyFailed(
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

  // A flashcard set or a video, from its row: the same question and answers as in its own editor.
  async function handleDeleteMedia(kind: MediaKind, id: string, focus: string | null, running: boolean) {
    const name = focus ?? "Toàn bộ tài liệu";
    if (running) {
      notifyFailed(`${kind} "${name}" đang được tạo. Chờ xong rồi xóa.`);
      return;
    }
    const video = kind === "Video";
    const confirmed = await confirmAction({
      title: video ? `Xóa video "${name}"?` : `Xóa bộ flashcard "${name}"?`,
      description: video ? "Slide, hình và giọng đã tạo bị xóa theo. Không thể khôi phục." : "Không thể khôi phục.",
      action: video ? "Xóa video" : "Xóa bộ flashcard", destructive: true,
    });
    if (!confirmed) return;
    setDeletingMediaId(id);
    try {
      await (video ? deleteVideo(id) : deleteFlashcardSet(id));
    } catch (err) {
      notifyFailed(apiErrorMessage(err, video ? "Xóa video thất bại" : "Xóa bộ flashcard thất bại"));
      setDeletingMediaId(null);
      return;
    }
    notifyDone(video ? `Đã xóa video "${name}".` : `Đã xóa bộ flashcard "${name}".`);
    try {
      if (video) setVideos(await listVideos(courseId ?? ""));
      else setFlashcardSets(await listFlashcardSets(courseId ?? ""));
    } catch (err) {
      notifyFailed(`${apiErrorMessage(err, "Không tải lại được danh sách")}. Đã xóa, hãy tải lại trang để thấy danh sách mới.`);
    } finally {
      setDeletingMediaId(null);
    }
  }

  async function handleDeleteModule(m: ModuleSummary) {
    const confirmed = await confirmAction({
      title: `Xóa mô-đun "${m.title}"?`,
      description: `${m.is_control ? "" : "Bản đối chứng đi kèm, nếu có, cũng bị xóa. "}Nội dung đã đẩy lên Moodle vẫn giữ nguyên. Không thể khôi phục.`,
      action: "Xóa mô-đun", destructive: true,
    });
    if (!confirmed) return;

    setDeletingModuleId(m.id);
    try {
      await deleteModule(m.id);
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Xóa mô-đun thất bại"));
      setDeletingModuleId(null);
      return;
    }
    // The module is gone whatever happens next: a list that cannot be reloaded is a different
    // problem, and saying the delete failed would send the lecturer to delete it again.
    notifyDone(`Đã xóa mô-đun "${m.title}".`);
    try {
      await refresh();
    } catch (err) {
      notifyFailed(
        `${apiErrorMessage(err, "Không tải lại được danh sách")}. Mô-đun đã được xóa, hãy tải lại trang để thấy danh sách mới.`,
      );
    } finally {
      setDeletingModuleId(null);
    }
  }

  return (
    <AppShell title={course?.name ?? "Tài liệu môn học"}>
      <GenerateDialog {...dialogProps("module")} onGenerate={(language, instructions, { modelAlias }) => {
        if (asking) void handleGenerate(asking.document, language, instructions, modelAlias);
      }} />
      <GenerateDialog {...dialogProps("flashcards")} onGenerate={(language, focus, { modelAlias }) => {
        if (asking) void handleGenerateFlashcards(asking.document, language, focus, modelAlias);
      }} />
      <GenerateDialog {...dialogProps("video")} onGenerate={(language, focus, choice) => {
        if (asking) void handleGenerateVideo(asking.document, language, focus, choice);
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
              ? "Đang xử lý tài liệu…"
              : "Tải tài liệu lên"}
          <input
            type="file"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              upload.setValue("file", file);
              void upload.handleSubmit(handleUpload, (errors) => { if (errors.file?.message) notifyFailed(errors.file.message); })();
            }}
            disabled={loading || uploadBusy}
            accept=".pdf,.pptx,.docx,.html,.htm,.mp3,.wav,.m4a,.flac,.ogg,.aac,.mp4,.mkv,.webm,.mov,.avi"
          />
        </label>
      </div>

      {!loading && documents.length > 0 && (
        <div className="course-filter">
          <label htmlFor="document-status-filter">Trạng thái</label>
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
        <p className="course-empty">Đang tải tài liệu…</p>
      ) : loadFailed ? (
        <div className="course-empty">
          <p role="alert" className="danger-text">Không tải được tài liệu của môn học.</p>
          <button type="button" onClick={retryLoad} className="ui-button mt-3">Thử lại</button>
        </div>
      ) : (
        <ul className="doc-list">
          {visibleDocuments.map((d) => (
            <li key={d.id} className="doc-row">
              <div className="doc-head">
                <div className="doc-title">
                  <div className="doc-name">{d.filename}</div>
                  {/* A ready document needs no word, as a ready module, set or video needs none. */}
                  {d.status !== "embedded" && (
                    <div className="status-pill" data-status={d.status}>
                      {STATUS_LABEL[d.status] ?? d.status}
                    </div>
                  )}
                </div>
                <div className="doc-actions">
                  {d.status === "embedded" && (
                    <DropdownMenu>
                      <DropdownMenuTrigger className="ui-button doc-create" aria-label={`Tạo nội dung từ ${d.filename}`}>
                        <Plus aria-hidden="true" />Tạo nội dung<ChevronDown aria-hidden="true" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="doc-menu">
                        <DropdownMenuItem disabled={isGenerating(d)} onSelect={() => ask("module", d)}>
                          <BookOpenText aria-hidden="true" />Mô-đun
                        </DropdownMenuItem>
                        <DropdownMenuItem disabled={isSending(d, "Flashcard")} onSelect={() => ask("flashcards", d)}>
                          <Layers aria-hidden="true" />Flashcard
                        </DropdownMenuItem>
                        <DropdownMenuItem disabled={isSending(d, "Video")} onSelect={() => ask("video", d)}>
                          <Clapperboard aria-hidden="true" />Video
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                  <DropdownMenu>
                    <DropdownMenuTrigger className="icon-button" aria-label={`Thao tác khác với ${d.filename}`}>
                      <Ellipsis aria-hidden="true" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="doc-menu">
                      <DropdownMenuItem variant="destructive" disabled={deletingDocId === d.id} onSelect={() => handleDeleteDocument(d)}>
                        <Trash2 aria-hidden="true" />{deletingDocId === d.id ? "Đang xóa…" : "Xóa tài liệu"}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
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
                        {f.error && <div className="mt-0.5 break-words text-sm leading-[19px]">{f.error}</div>}
                      </div>
                      <button
                        onClick={() => handleRetry(f)}
                        disabled={retryingJobIds.has(f.job_id)}
                        aria-label={`Thử lại: ${failureTitle(f)} (${d.filename})`}
                        className="link-action shrink-0"
                      >
                        {retryingJobIds.has(f.job_id) ? "Đang thử lại…" : "Thử lại"}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {((modulesByDoc[d.id] ?? []).length > 0 || isGenerating(d) || setsByDoc(d.id).length > 0
                || isSending(d, "Flashcard") || videosByDoc(d.id).length > 0 || isSending(d, "Video")) && (
                <ul className="doc-modules" aria-label={`Nội dung tạo từ ${d.filename}`}>
                  {isGenerating(d) && <PendingModuleItem />}
                  {(modulesByDoc[d.id] ?? []).map((m) => (
                    <ModuleListItem
                      key={m.id}
                      module={m}
                      deleting={deletingModuleId === m.id}
                      onDelete={handleDeleteModule}
                    />
                  ))}
                  {/* The newest come first, which is where the item being asked for will be. */}
                  {pendingFor(d, "Flashcard").map((item) => <PendingMediaItem key={item.key} item={item} />)}
                  {setsByDoc(d.id).map((set) => (
                    <MediaListItem
                      key={set.id}
                      kind="Flashcard"
                      to={`/flashcard-sets/${set.id}`}
                      focus={set.focus}
                      status={mediaPillStatus(set.status === "generating", set.status === "failed")}
                      statusText={flashcardStatusText(set)}
                      created={setDates.get(set.id)}
                      deleting={deletingMediaId === set.id}
                      onDelete={() => void handleDeleteMedia("Flashcard", set.id, set.focus, set.status === "generating")}
                    />
                  ))}
                  {pendingFor(d, "Video").map((item) => <PendingMediaItem key={item.key} item={item} />)}
                  {videosByDoc(d.id).map((video) => (
                    <MediaListItem
                      key={video.id}
                      kind="Video"
                      to={`/videos/${video.id}`}
                      focus={video.focus}
                      status={mediaPillStatus(VIDEO_JOB_TEXT[video.status] !== undefined, video.status === "failed")}
                      statusText={videoStatusText(video)}
                      created={videoDates.get(video.id)}
                      deleting={deletingMediaId === video.id}
                      onDelete={() => void handleDeleteMedia("Video", video.id, video.focus, VIDEO_JOB_TEXT[video.status] !== undefined)}
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
              <p className="course-empty">Cần có ít nhất 1 tài liệu đã xử lý xong để hỏi đáp.</p>
            </section>
          )}
      </div>
      </div>
    </AppShell>
  );
}

function MoodleLinkSection({ course, onLinked }: { course: Course; onLinked: () => void }) {
  // The account is linked from the account menu; here it only decides whether courses can be picked.
  // "unknown" until the server answers, and the server refuses a course link without the account anyway.
  const [account, setAccount] = useState<MoodleAccountState>("unknown");
  useEffect(() => {
    let cancelled = false;
    const read = () => getMoodleAccount().then(
      (answer) => { if (!cancelled) setAccount(answer.status); },
      () => { if (!cancelled) setAccount("unknown"); },
    );
    void read();
    window.addEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, read);
    return () => {
      cancelled = true;
      window.removeEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, read);
    };
  }, []);
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
        if (!cancelled) setError(apiErrorMessage(err, "Không tải được danh sách khóa học Moodle"));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [linked, account]);

  async function handleLink({ moodleCourseId }: LinkMoodleCourseFormValues) {
    setBusy(true);
    try {
      await linkMoodleCourse(course.id, Number(moodleCourseId));
      onLinked();
      notifyDone(`Đã liên kết với khóa học Moodle #${moodleCourseId}.`);
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Liên kết khóa học thất bại"));
    } finally {
      setBusy(false);
    }
  }

  // Only a choice to make earns a line: a linked course, or an account still to link, shows nothing,
  // since the account menu holds the Moodle account.
  if (linked || account !== "linked") return null;

  const panel = (row: ReactNode) => (
    <div className="moodle-panel">
      <div className="moodle-course">{row}</div>
      {error && <p className="moodle-error danger-text">{error}</p>}
    </div>
  );

  return panel(
    <form
      onSubmit={handleSubmit(handleLink, (errors) => setError(errors.moodleCourseId?.message ?? null))}
      noValidate
      className="moodle-link-form"
    >
      <span className="moodle-key">Khóa học Moodle:</span>{" "}
        <>
          <select
            aria-label="Khóa học Moodle"
            {...register("moodleCourseId")}
            className="ui-select min-w-0 flex-1 basis-56"
          >
            <option value="">Chọn khóa học Moodle</option>
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
    </form>,
  );
}
