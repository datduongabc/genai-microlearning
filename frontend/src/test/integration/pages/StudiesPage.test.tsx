import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { StudiesPage, BlindRatingPage } from "../../../pages/StudiesPage";
import { AdminStudiesPanel } from "../../../components/AdminStudiesPanel";
import * as api from "../../../api/studies";
import * as auth from "../../../auth/useAuth";

vi.mock("../../../api/studies");
const study = {
  id: "s1",
  name: "Nghiên cứu",
  rubric_version: "v1",
  status: "draft",
  created_at: "",
};
const question = {
  stem: "Câu hỏi",
  choices: ["Đúng", "Sai"],
  correct_index: 0,
  explanation: "Lời giải",
};
const item = {
  id: "i1",
  anon_code: "M01",
  rating: null,
  packet: {
    anon_id: "M01",
    topic: "Chủ đề",
    bloom_level: "understand",
    learning_objective: "Mục tiêu",
    sub_objectives: ["Mục tiêu con"],
    content_markdown: "Nội dung bài",
    slides: [{ title: "Slide", bullets: ["Ý chính"] }],
    examples: [{ context: "Bối cảnh", analysis: "Phân tích" }],
    quiz: [question],
    practice: question,
    source_chunks: ["Nguồn đối chiếu"],
  },
};
const data = { study, items: [item], rated: 0, total: 1 };
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(auth, "useAuth").mockReturnValue({
    user: { id: "u1", name: "Lan", email: "lan@example.com", is_admin: false },
    token: "t",
    refreshToken: null,
    login: vi.fn(),
    logout: vi.fn(),
  });
  vi.mocked(api.myStudies).mockResolvedValue([{ ...study, rated: 0, total: 1 }]);
  vi.mocked(api.myStudyItems).mockResolvedValue(data);
  vi.mocked(api.listStudies).mockResolvedValue([study]);
  vi.mocked(api.getRaters).mockResolvedValue({ emails: ["lan@example.com"] });
});
function blind() {
  return render(
    <MemoryRouter initialEntries={["/studies/s1/items/i1"]}>
      <Routes>
        <Route path="/studies/:studyId/items/:itemId" element={<BlindRatingPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
it("lists invitations with personal progress", async () => {
  render(
    <MemoryRouter>
      <StudiesPage />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: /Nghiên cứu/ })).toHaveAttribute(
    "href",
    "/studies/s1",
  );
  expect(screen.getByText("Đã chấm 0/1")).toBeInTheDocument();
});
it("renders the whole anonymous packet and saves/edits all rubric criteria", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveRating).mockImplementation(async (_s, _i, body) => body);
  blind();
  expect(await screen.findByText("Nội dung bài")).toBeInTheDocument();
  for (const text of ["Nguồn đối chiếu", "Mục tiêu con", "Ý chính", "Bối cảnh", "Phân tích"])
    expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.getAllByText("Lời giải")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  expect(api.saveRating).not.toHaveBeenCalled();
  for (const select of screen.getAllByRole("combobox", { name: /Điểm/ }))
    await user.selectOptions(select, "4");
  for (const field of screen.getAllByRole("textbox", { name: /Nhận xét/ }))
    await user.type(field, "Có căn cứ");
  await user.type(screen.getByLabelText("Ghi chú tổng quan"), "Tổng quan");
  await user.click(screen.getByRole("button", { name: "Thêm claim không có căn cứ" }));
  await user.type(screen.getByLabelText("Claim 1"), "Khẳng định");
  await user.type(screen.getByLabelText("Lý do 1"), "Không có nguồn");
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  expect(await screen.findByText("Đã lưu đánh giá.")).toBeInTheDocument();
  const body = vi.mocked(api.saveRating).mock.calls[0][2];
  expect(body.accuracy).toEqual({ score: 4, justification: "Có căn cứ" });
  expect(body.hallucination_findings).toEqual([
    { claim: "Khẳng định", field: "content", reason: "Không có nguồn" },
  ]);
  expect(body.overall_notes).toBe("Tổng quan");
  expect(screen.getByText("Đã chấm 1/1")).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Điểm Độ chính xác"), "2");
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  await waitFor(() =>
    expect(api.saveRating).toHaveBeenLastCalledWith(
      "s1",
      "i1",
      expect.objectContaining({ accuracy: { score: 2, justification: "Có căn cứ" } }),
    ),
  );
});
it("keeps edits and progress unchanged after a failed save", async () => {
  const score = { score: 3, justification: "Đã chấm" };
  const rating = {
    anon_id: "M01",
    accuracy: score,
    completeness: score,
    consistency: score,
    hallucination_score: score,
    slide_quality: score,
    quiz_quality: score,
    bloom_fidelity: score,
    query_alignment: score,
    overall_notes: "Cũ",
    hallucination_findings: [],
  };
  vi.mocked(api.myStudyItems).mockResolvedValue({
    ...data,
    rated: 1,
    items: [{ ...item, rating }],
  });
  vi.mocked(api.saveRating).mockRejectedValue(new Error("offline"));
  blind();
  await screen.findByText("Nội dung bài");
  await userEvent.type(screen.getByLabelText("Ghi chú tổng quan"), " mới");
  await userEvent.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Không lưu được đánh giá.");
  expect(screen.getByLabelText("Ghi chú tổng quan")).toHaveValue("Cũ mới");
});
it("reports missing or revoked invitations", async () => {
  vi.mocked(api.myStudyItems).mockRejectedValue(new Error("404"));
  blind();
  expect(await screen.findByRole("alert")).toHaveTextContent("Không tải được nghiên cứu");
  expect(screen.queryByRole("button", { name: "Lưu đánh giá" })).not.toBeInTheDocument();
});
it("admin edits invited emails and can remove all raters", async () => {
  vi.mocked(api.setRaters).mockImplementation(async (_s, emails) => ({ emails }));
  render(<AdminStudiesPanel />);
  await userEvent.selectOptions(await screen.findByLabelText("Nghiên cứu"), "s1");
  const input = await screen.findByLabelText("Email người chấm (mỗi dòng một email)");
  await waitFor(() => expect(input).toHaveValue("lan@example.com"));
  await userEvent.clear(input);
  await userEvent.type(input, "bad");
  await userEvent.click(screen.getByRole("button", { name: "Lưu người chấm" }));
  expect(api.setRaters).not.toHaveBeenCalled();
  await userEvent.clear(input);
  await userEvent.click(screen.getByRole("button", { name: "Lưu người chấm" }));
  await waitFor(() => expect(api.setRaters).toHaveBeenCalledWith("s1", []));
});
