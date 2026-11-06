import { useParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Brand } from "../components/AuthLayout";
import { CourseChat } from "../components/CourseChat";

// Everything a student account an LMS made can use: the chat of the course it was opened from. The
// course is known only from the launch; the student cannot list courses or read anything else of one.
export function LearnerChatPage() {
  const { courseId } = useParams();
  const { user, logout } = useAuth();

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <header className="mb-6 flex items-center justify-between gap-4">
        <Brand />
        <div className="flex items-center gap-3 text-sm">
          {user && <span className="text-gray-600">{user.name}</span>}
          <button type="button" onClick={logout} className="rounded border border-gray-300 px-3 py-1.5">
            Đăng xuất
          </button>
        </div>
      </header>
      <main>
        <h1 className="mb-1 text-lg font-semibold">Hỏi đáp về tài liệu môn học</h1>
        {courseId ? (
          <>
            <p className="mb-4 text-sm text-gray-600">
              Câu trả lời dựa trên tài liệu giảng viên đã tải lên và ghi rõ nguồn.
            </p>
            <CourseChat key={courseId} courseId={courseId} />
          </>
        ) : (
          <p role="status" className="text-sm text-gray-600">
            Hãy mở công cụ từ hoạt động trong khóa học trên Moodle.
          </p>
        )}
      </main>
    </div>
  );
}
