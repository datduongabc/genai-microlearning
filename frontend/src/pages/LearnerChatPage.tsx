import { Fragment } from "react";
import { useParams } from "react-router-dom";
import { CourseChat } from "../components/CourseChat";
import { LearnItems } from "../components/LearnItems";
import { LearnerShell } from "../components/LearnerShell";
import { useDocumentTitle } from "../lib/documentTitle";

// Everything a student account an LMS made can use: what the lecturer published for the course it was
// opened from, and its chat. The course is known only from the launch; the student cannot list courses.
export function LearnerChatPage() {
  useDocumentTitle("Học theo tài liệu môn học");
  const { courseId } = useParams();

  return (
    <LearnerShell>
      <h1 className="page-title">Học theo tài liệu môn học</h1>
      {courseId ? (
        <Fragment key={courseId}>
          <LearnItems courseId={courseId} />
          <CourseChat courseId={courseId} />
        </Fragment>
      ) : (
        <p role="status" className="learner-lead">
          Hãy mở công cụ từ hoạt động trong khóa học trên Moodle.
        </p>
      )}
    </LearnerShell>
  );
}
