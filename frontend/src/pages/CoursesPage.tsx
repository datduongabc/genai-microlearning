import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { createCourse, deleteCourse, listCourses, type Course } from "../api";
import { AppShell } from "../components/AppShell";

export function CoursesPage() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

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
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    try {
      await createCourse(name.trim());
      setName("");
      await refresh();
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(course: Course) {
    // Xóa vĩnh viễn cả tài liệu/mô-đun/quiz trong course (cascade ở DB) --
    // không đụng tới Moodle dù course đã liên kết, nên phải cảnh báo rõ.
    const confirmed = window.confirm(
      `Xóa vĩnh viễn môn học "${course.name}"? Toàn bộ tài liệu, mô-đun và quiz đã sinh trong môn học này sẽ bị xóa theo, không thể khôi phục.${
        course.moodle_course_id !== null
          ? " (Course trên Moodle sẽ KHÔNG bị xóa, chỉ mất liên kết.)"
          : ""
      }`,
    );
    if (!confirmed) return;

    setDeletingId(course.id);
    try {
      await deleteCourse(course.id);
      await refresh();
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <AppShell title="Môn học">
      <form onSubmit={handleCreate} className="mb-8 flex gap-3">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Tên môn học mới"
          className="flex-1 border-0 border-b-[1.5px] border-gray-300 bg-transparent py-2 text-[15px] outline-none focus:border-terracotta"
        />
        <button
          disabled={creating}
          className="rounded-[3px] bg-terracotta px-5 text-sm font-semibold text-white hover:bg-terracotta-dark disabled:opacity-60"
        >
          {creating ? "Đang tạo..." : "Tạo môn học"}
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-gray-500">Đang tải...</p>
      ) : courses.length === 0 ? (
        <p className="text-sm text-gray-500">Chưa có môn học nào.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-gray-200">
          {courses.map((c) => (
            <li key={c.id} className="flex items-center justify-between py-4">
              <Link
                to={`/courses/${c.id}`}
                className="font-medium hover:text-terracotta"
              >
                {c.name}
              </Link>
              <button
                onClick={() => handleDelete(c)}
                disabled={deletingId === c.id}
                className="text-sm text-red-700 hover:underline disabled:opacity-60"
              >
                {deletingId === c.id ? "Đang xóa..." : "Xóa"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
