import { Ellipsis, Trash2 } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../components/ui/dropdown-menu";
import { confirmAction } from "../lib/confirm";
import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "react-router-dom";
import { apiErrorMessage, createCourse, deleteCourse, listCourses, type Course } from "../api";
import { AppShell } from "../components/AppShell";
import { createCourseSchema, type CreateCourseFormValues } from "../lib/formSchemas";
import { notifyDone, notifyFailed } from "../lib/notify";

const RELOAD_FAILED = "Không tải lại được danh sách môn học, hãy tải lại trang.";

export function CoursesPage() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  // A failed load must not fall through to the empty state: "no courses yet" would tell the lecturer
  // their courses are gone and invite them to create a duplicate.
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting: creating },
  } = useForm<CreateCourseFormValues>({ resolver: zodResolver(createCourseSchema) });

  const refresh = useCallback(async () => {
    setCourses(await listCourses());
  }, []);

  // The first load sets state from promise callbacks, and drops a result that
  // arrives after the page has gone; refresh() is for reloading after a change.
  useEffect(() => {
    let cancelled = false;
    listCourses()
      .then((list) => {
        if (!cancelled) setCourses(list);
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  function retryLoad() {
    setLoading(true);
    setLoadFailed(false);
    setAttempt((n) => n + 1);
  }

  async function handleCreate(values: CreateCourseFormValues) {
    try {
      await createCourse(values.name);
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Tạo môn học thất bại"));
      return;
    }
    reset();
    notifyDone(`Đã tạo môn học "${values.name}".`);
    await refresh().catch(() => notifyFailed(RELOAD_FAILED));
  }

  async function handleDelete(course: Course) {
    // Xóa vĩnh viễn cả tài liệu/mô-đun/quiz trong course (cascade ở DB) --
    // không đụng tới Moodle dù course đã liên kết, nên phải cảnh báo rõ.
    const confirmed = await confirmAction({
      title: `Xóa môn học "${course.name}"?`,
      description: `Toàn bộ tài liệu, mô-đun, quiz, flashcard và video trong môn học bị xóa theo. Không thể khôi phục.${
        course.moodle_course_id !== null ? " Khóa học trên Moodle vẫn giữ nguyên." : ""
      }`,
      action: "Xóa môn học", destructive: true,
    });
    if (!confirmed) return;

    setDeletingId(course.id);
    try {
      await deleteCourse(course.id);
    } catch (err) {
      // A refusal (a job still running, a module in a study) said nothing before: the row just stayed.
      notifyFailed(apiErrorMessage(err, "Xóa môn học thất bại"));
      setDeletingId(null);
      return;
    }
    // Deleted whatever happens next: a list that cannot be read again must not say the delete failed.
    notifyDone(`Đã xóa môn học "${course.name}".`);
    await refresh().catch(() => notifyFailed(RELOAD_FAILED));
    setDeletingId(null);
  }

  return (
    <AppShell title="Môn học">
      <form onSubmit={handleSubmit(handleCreate)} noValidate className="course-create">
        <input
          {...register("name")}
          aria-label="Tên môn học mới"
          placeholder="Ví dụ: Hệ điều hành"
          className="ui-input min-w-0 flex-1"
        />
        <button disabled={creating} className="ui-button primary">
          {creating ? "Đang tạo…" : "Tạo môn học"}
        </button>
        {errors.name && <p className="basis-full text-sm danger-text">{errors.name.message}</p>}
      </form>

      {loading ? (
        <p className="course-empty">Đang tải môn học…</p>
      ) : loadFailed ? (
        <div className="course-empty">
          <p role="alert" className="danger-text">Không tải được danh sách môn học.</p>
          <button type="button" onClick={retryLoad} className="ui-button mt-3">Thử lại</button>
        </div>
      ) : courses.length === 0 ? (
        <p className="course-empty">Chưa có môn học. Tạo môn học đầu tiên ở ô bên trên.</p>
      ) : (
        <ul className="course-list">
          {courses.map((c) => (
            <li key={c.id} className="course-row">
              <span className="course-tile" aria-hidden="true">{c.name.trim().charAt(0).toUpperCase()}</span>
              <div className="min-w-0">
                <Link to={`/courses/${c.id}`} className="course-name">
                  {c.name}
                </Link>
                {/* Most courses are not linked, so only a link is worth a line. */}
                {c.moodle_course_id !== null && <div className="course-meta">Đã liên kết khóa học Moodle #{c.moodle_course_id}</div>}
              </div>
              {/* Delete sits behind a menu: a red word on every row pulled the eye to the riskiest action. */}
              <DropdownMenu>
                <DropdownMenuTrigger className="icon-button" aria-label={`Thao tác khác với ${c.name}`}>
                  <Ellipsis aria-hidden="true" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="doc-menu">
                  <DropdownMenuItem variant="destructive" disabled={deletingId === c.id} onSelect={() => void handleDelete(c)}>
                    <Trash2 aria-hidden="true" />{deletingId === c.id ? "Đang xóa…" : "Xóa môn học"}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
