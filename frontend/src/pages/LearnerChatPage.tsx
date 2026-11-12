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
    <div className="learner-page">
      <header className="learner-header">
        <Brand />
        <div className="learner-user">
          {user && <span className="muted">{user.name}</span>}
          <button type="button" onClick={logout} className="ui-button">
            Đăng xuất
          </button>
        </div>
      </header>
      <main className="learner-main">
        <h1 className="page-title">Hỏi đáp về tài liệu môn học</h1>
        {courseId ? (
          <>
            <CourseChat key={courseId} courseId={courseId} />
          </>
        ) : (
          <p role="status" className="learner-lead">
            Hãy mở công cụ từ hoạt động trong khóa học trên Moodle.
          </p>
        )}
      </main>
    </div>
  );
}
