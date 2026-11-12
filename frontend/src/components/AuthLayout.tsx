import type { ReactNode } from "react";

export function Brand() {
  return <div className="brand"><span className="brand-logo" aria-hidden="true">G</span><div><div className="brand-name">GENAILMS</div><div className="brand-sub">Microlearning từ tài liệu</div></div></div>;
}

export function AuthLayout({ children }: { children: ReactNode }) {
  return <div className="auth-layout">
    <aside className="auth-brand">
      <Brand />
      <div className="auth-pitch">
        <h2>Biến tài liệu môn học thành bài học ngắn, có trích dẫn nguồn.</h2>
        {/* What reviewing a module looks like: a sentence of the lesson, and the passage of the
            lecturer's own slides it was written from. Illustration only. */}
        <figure className="auth-sample" aria-hidden="true">
          <p className="auth-sample-text">
            <mark>Tiến trình là một chương trình đang được thực thi</mark>, gồm mã lệnh, dữ liệu và trạng thái của CPU.<sup>1</sup>
          </p>
          <figcaption className="auth-sample-source">
            <span>Nguồn 1: Bài giảng Hệ điều hành, slide 12</span>
            A process is a program in execution.
          </figcaption>
        </figure>
        {/* The three steps of the work, in the order a lecturer does them. */}
        <ol className="auth-steps">
          <li>Sinh bài học 7 phần từ tài liệu của bạn</li>
          <li>Bấm số trích dẫn để xem đoạn gốc</li>
          <li>Duyệt, sửa, rồi đưa lên Moodle</li>
        </ol>
      </div>
    </aside>
    <main className="auth-area">{children}</main>
  </div>;
}
