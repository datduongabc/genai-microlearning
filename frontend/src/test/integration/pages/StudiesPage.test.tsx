import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { StudiesPage, BlindRatingPage } from "../../../pages/StudiesPage";
import { AdminStudiesPanel } from "../../../components/AdminStudiesPanel";
import * as api from "../../../api/studies";
import * as admin from "../../../api/admin";
import type { Rating } from "../../../lib/ratingSchema";
import * as auth from "../../../auth/useAuth";

vi.mock("../../../api/studies");
vi.mock("../../../api/admin");
const study = {
  id: "s1",
  name: "Nghiên cứu",
  rubric_version: "v3",
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
  kind: "module" as const,
  rating: null,
  reference: null,
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
const noResults = { criteria: [], total: 0, raters: [], has_judge: false, has_media_ratings: false, unpaired: [], items: [] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(auth, "useAuth").mockReturnValue({
    user: { id: "u1", name: "Lan", email: "lan@example.com", is_admin: false },
    token: "t",
    refreshToken: null,
    login: vi.fn(),
    loginWithLaunchCode: vi.fn(),
    logout: vi.fn(),
  });
  vi.mocked(api.myStudies).mockResolvedValue([{ ...study, rated: 0, total: 1 }]);
  vi.mocked(api.myStudyItems).mockResolvedValue(data);
  // Two studies, so the list is shown; with one it opens by itself.
  vi.mocked(api.listStudies).mockResolvedValue([study, { ...study, id: "s2", name: "Khác" }]);
  vi.mocked(api.getRaters).mockResolvedValue({ emails: ["lan@example.com"], assignments: {} });
  vi.mocked(api.listPairCandidates).mockResolvedValue([]);
  vi.mocked(api.listMediaCandidates).mockResolvedValue([]);
  vi.mocked(api.getStudyResults).mockResolvedValue(noResults);
  vi.mocked(api.listStudyFiles).mockResolvedValue([]);
  vi.mocked(api.getStudyItems).mockResolvedValue([]);
  const account = { is_admin: false, is_active: true, created_at: "", moodle_status: null };
  vi.mocked(admin.listUsers).mockResolvedValue([
    { ...account, id: "u1", email: "lan@example.com", name: "Lan" },
    { ...account, id: "u2", email: "minh@example.com", name: "Minh Trần" },
  ] as admin.AdminUser[]);
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
  // The sidebar's own "Nghiên cứu" link goes to /studies; the study's link is the other one.
  expect(await screen.findByText("Đã chấm 0/1")).toBeInTheDocument();
  const links = screen.getAllByRole("link", { name: /Nghiên cứu/ });
  expect(links.map((link) => link.getAttribute("href"))).toContain("/studies/s1");
});
// It types into all nine rubric comments, about 1.6 s alone: under the full parallel run it passed 5 s.
it("renders the whole anonymous packet and saves/edits all rubric criteria", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveRating).mockImplementation(async (_s, _i, body) => body);
  blind();
  expect(await screen.findByText("Mục tiêu con")).toBeInTheDocument();
  for (const [tab, text] of [["Mục tiêu", "Mục tiêu con"], ["Nội dung", "Nội dung bài"], ["Slide", "Ý chính"], ["Ví dụ", "Bối cảnh"], ["Luyện tập", "Câu hỏi"], ["Quiz", "Câu hỏi"]]) {
    await user.click(screen.getByRole("tab", { name: tab }));
    expect(within(screen.getByRole("tabpanel")).getByText(text)).toBeInTheDocument();
  }
  await user.click(screen.getByRole("tab", { name: "Giải thích đáp án" }));
  expect(screen.getAllByText("Lời giải")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  expect(api.saveRating).not.toHaveBeenCalled();
  for (const group of screen.getAllByRole("radiogroup", { name: /Điểm/ }))
    await user.click(within(group).getByRole("radio", { name: "4" }));
  for (const field of screen.getAllByRole("textbox", { name: /Nhận xét/ }))
    await user.type(field, "Có căn cứ");
  // The quiz score is computed from the flaws marked: two on one of the two questions halves it.
  expect(screen.queryByRole("radiogroup", { name: "Điểm Chất lượng quiz" })).not.toBeInTheDocument();
  expect(screen.getByText("Điểm tự tính: 5/5")).toBeInTheDocument();
  const first = screen.getByText("Câu 1").closest("details")!;
  // Each question is named with its stem, so the rater need not look it up in the Quiz tab.
  expect(first.querySelector("summary")).toHaveTextContent("Câu 1Câu hỏi");
  await user.click(screen.getByText("Câu 1"));
  await user.click(within(first).getByLabelText("Dùng từ tuyệt đối (luôn luôn, không bao giờ, tất cả)"));
  await user.click(within(first).getByLabelText("Có phương án nhiễu vô lý"));
  expect(screen.getByText("2 lỗi")).toBeInTheDocument();
  expect(screen.getByText("Điểm tự tính: 3/5")).toBeInTheDocument();
  await user.click(screen.getByText("Ghi chú chung"));
  await user.type(screen.getByLabelText("Ghi chú tổng quan"), "Tổng quan");
  expect(screen.getByRole("heading", { name: "Khẳng định không có căn cứ 0" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Thêm khẳng định không có căn cứ" }));
  expect(screen.getByRole("heading", { name: "Khẳng định không có căn cứ 1" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Khẳng định 1"), "Khẳng định");
  await user.type(screen.getByLabelText("Lý do 1"), "Không có nguồn");
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  expect(await screen.findByText("Đã lưu đánh giá.")).toBeInTheDocument();
  const body = vi.mocked(api.saveRating).mock.calls[0][2] as Rating;
  expect(body.accuracy).toEqual({ score: 4, justification: "Có căn cứ" });
  expect(body.examples_feedback).toEqual({ score: 4, justification: "Có căn cứ" });
  expect(body.quiz_quality).toEqual({ score: 3, justification: "Có căn cứ" });
  expect(body.quiz_flaws.map((q) => [q.question, [...q.flaws].sort()])).toEqual([
    [1, ["absolute_terms", "implausible_distractors"]], [2, []],
  ]);
  expect(body.hallucination_findings).toEqual([
    { claim: "Khẳng định", field: "content", reason: "Không có nguồn" },
  ]);
  expect(body.overall_notes).toBe("Tổng quan");
  expect(screen.getByText("Đã chấm 1/1")).toBeInTheDocument();
  await user.click(within(screen.getByRole("radiogroup", { name: "Điểm Độ chính xác" })).getByRole("radio", { name: "2" }));
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  await waitFor(() =>
    expect(api.saveRating).toHaveBeenLastCalledWith(
      "s1",
      "i1",
      expect.objectContaining({ accuracy: { score: 2, justification: "Có căn cứ" } }),
    ),
  );
}, 15_000);

it("shows breadcrumb, tabs, Bloom placement, hidden quiz answers, missing counter, failed save criterion focus, and finding locations", async () => {
  const user = userEvent.setup();
  blind();

  const trail = await screen.findByRole("navigation", { name: "Đường dẫn" });
  expect(trail).toHaveTextContent("Nghiên cứu / Nghiên cứu / M01");

  expect(screen.getByText("Mục tiêu con")).toBeVisible();
  // Raters check against the reference file; a tab of passages meant nothing to them.
  expect(screen.queryByRole("tab", { name: /Đoạn nguồn/ })).not.toBeInTheDocument();
  expect(screen.queryByText("Nguồn đối chiếu")).not.toBeInTheDocument();
  screen.getByRole("tab", { name: "Mục tiêu" }).focus();
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Nội dung" })).toHaveFocus();
  expect(screen.getByText("Nội dung bài")).toBeVisible();

  const bloomCriterion = screen.getByText(/7\. Độ khớp mức Bloom/).closest("details")!;
  expect(within(bloomCriterion).getByText("Mức Bloom đặt ra: Hiểu")).toBeInTheDocument();
  expect(screen.getAllByText(/Mức Bloom đặt ra/)).toHaveLength(1);
  for (const tab of screen.getAllByRole("tab")) {
    await user.click(tab);
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).queryByText(/Mức Bloom|Hiểu|understand/)).not.toBeInTheDocument();
    if (tab.textContent === "Quiz") {
      expect(within(panel).getByText("Câu hỏi")).toBeInTheDocument();
      expect(screen.queryByLabelText("Đáp án đúng")).not.toBeInTheDocument();
      expect(screen.queryByText("Lời giải")).not.toBeInTheDocument();
    } else if (tab.textContent === "Giải thích đáp án") {
      expect(screen.getAllByLabelText("Đáp án đúng")).toHaveLength(2);
    }
  }

  expect(screen.getByText("Còn 9 tiêu chí")).toBeInTheDocument();
  const accuracy = screen.getByRole("radiogroup", { name: "Điểm Độ chính xác" });
  await user.click(within(accuracy).getByRole("radio", { name: "5" }));
  expect(screen.getByText("Còn 9 tiêu chí")).toBeInTheDocument();
  await user.type(screen.getByRole("textbox", { name: "Nhận xét Độ chính xác" }), "Khớp nguồn");
  expect(screen.getByText("Còn 8 tiêu chí")).toBeInTheDocument();

  const criterion3 = screen.getByText(/3\. Tính nhất quán/).closest("details")!;
  expect(criterion3).not.toHaveAttribute("open");
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  await waitFor(() => expect(criterion3).toHaveAttribute("open"));
  await user.click(within(criterion3).getByRole("radio", { name: "4" }));
  await user.type(within(criterion3).getByRole("textbox", { name: /Nhận xét/ }), "C");
  await waitFor(() => expect(within(criterion3).queryByRole("alert")).not.toBeInTheDocument());
  expect(criterion3).toHaveAttribute("open");
  expect(within(criterion3).getByText(/động từ của mục tiêu/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /Thêm/ }));
  const location = screen.getByRole("combobox", { name: /Vị trí 1/ });
  expect(within(location).getAllByRole("option").map((o) => o.textContent)).toEqual(
    ["Nội dung", "Slide", "Ví dụ", "Quiz", "Bài luyện tập", "Lời giải thích"],
  );
});

it("shows no module form on a study made under an older rubric", async () => {
  vi.mocked(api.myStudyItems).mockResolvedValue({ ...data, study: { ...study, rubric_version: "v1" } });
  blind();
  expect(await screen.findByText(/dùng rubric v1, không chấm thêm mô-đun/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Lưu đánh giá" })).not.toBeInTheDocument();
});

it("keeps edits after a failed save and blocks link, logout, or beforeunload until saved", async () => {
  const user = userEvent.setup();
  const { logout } = auth.useAuth();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
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
    examples_feedback: score,
    quiz_flaws: [{ question: 1, flaws: [] }, { question: 2, flaws: [] }],
    overall_notes: "Cũ",
    hallucination_findings: [],
  };
  vi.mocked(api.myStudyItems).mockResolvedValue({
    ...data,
    rated: 1,
    items: [{ ...item, rating }],
  });
  vi.mocked(api.saveRating)
    .mockRejectedValueOnce(new Error("offline"))
    .mockImplementation(async (_s, _i, body) => body);
  try {
    render(
      <MemoryRouter initialEntries={["/studies/s1/items/i1"]}>
        <Routes>
          <Route path="/studies/:studyId/items/:itemId" element={<BlindRatingPage />} />
          <Route path="/studies" element={<p>Danh sách</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText("Mục tiêu con");
    await user.click(screen.getByText("Ghi chú chung"));
    await user.type(screen.getByLabelText("Ghi chú tổng quan"), " mới");

    await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
    expect(await screen.findByText("Không lưu được đánh giá.")).toBeInTheDocument();
    expect(screen.getByLabelText("Ghi chú tổng quan")).toHaveValue("Cũ mới");

    await user.click(within(screen.getByRole("navigation", { name: "Đường dẫn" })).getByRole("link", { name: "Nghiên cứu" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /Giảng viên/ }));
    await user.click(screen.getByRole("button", { name: "Đăng xuất" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(logout).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Ghi chú tổng quan")).toHaveValue("Cũ mới");
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
    await screen.findByText("Đã lưu đánh giá.");
    const afterSave = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(afterSave);
    expect(afterSave.defaultPrevented).toBe(false);
    await user.click(screen.getByRole("button", { name: /Giảng viên/ }));
    await user.click(screen.getByRole("button", { name: "Đăng xuất" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(logout).toHaveBeenCalledTimes(1);
    await user.click(within(screen.getByRole("navigation", { name: "Đường dẫn" })).getByRole("link", { name: "Nghiên cứu" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Danh sách")).toBeInTheDocument();
  } finally {
    confirm.mockRestore();
  }
});

it("loads the raters again when the same study is picked after a failed load", async () => {
  vi.mocked(api.getRaters).mockRejectedValueOnce(new Error("offline"));
  render(<MemoryRouter><AdminStudiesPanel /></MemoryRouter>);
  const pick = await screen.findByRole("button", { name: /Nghiên cứu/ });
  await userEvent.click(pick);
  expect(await screen.findByRole("alert")).toHaveTextContent("Không tải được người chấm.");
  await userEvent.click(pick);
  expect(await screen.findByRole("region", { name: "Nội dung chấm" })).toBeInTheDocument();
  expect(api.getRaters).toHaveBeenCalledTimes(2);
});

it("navigates between packets without keeping old ones and offers only that packet's reference file", async () => {
  const second = {
    ...item,
    id: "i2",
    anon_code: "M02",
    packet: { ...item.packet, anon_id: "M02", sub_objectives: ["Mục tiêu của gói hai"] },
  };
  vi.mocked(api.myStudyItems).mockResolvedValue({
    ...data,
    items: [{ ...item, reference: { filename: "Bài giảng 3.pdf", size: 1258291 } }, second],
    total: 2,
  });
  vi.mocked(api.downloadPacketReference).mockResolvedValue();
  const user = userEvent.setup();
  blind();

  await screen.findByText("Mục tiêu con");
  expect(screen.getByText("Bài giảng 3.pdf")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Tải xuống" }));
  expect(api.downloadPacketReference).toHaveBeenCalledWith("s1", "i1");

  const packets = within(screen.getByRole("navigation", { name: "Gói chấm" }));
  await user.click(packets.getByRole("link", { name: /M02/ }));
  await screen.findByText("Mục tiêu của gói hai");
  expect(screen.queryByText("Bài giảng 3.pdf")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Tải xuống" })).not.toBeInTheDocument();

  await user.click(packets.getByRole("link", { name: /M01/ }));
  await screen.findByText("Mục tiêu con");
  // The packet left behind must be gone, not kept beside the one now rated.
  expect(screen.queryByText("Mục tiêu của gói hai")).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(1);
});

it("shows the admin each rater's progress, and per packet the scores, findings and group", async () => {
  const scores = Object.fromEntries(["accuracy", "completeness"].map((c) => [c, { score: 4, justification: `Lý do ${c}` }]));
  vi.mocked(api.getStudyResults).mockResolvedValue({
    criteria: ["accuracy", "completeness"], total: 2, has_judge: false, has_media_ratings: true, unpaired: [],
    raters: [
      { email: "lan@example.com", name: "Lan", number: 1, rated: 1, module_rated: 1, invited: true, assigned: 2 },
      { email: "minh@example.com", name: "Minh Trần", number: null, rated: 0, module_rated: 0, invited: true, assigned: 2 },
    ],
    items: [
      { id: "i1", anon_code: "M01", group: "rag_llm", pair_code: "M01", topic: "Chủ đề", criteria: ["accuracy", "completeness"], ratings: [{
        rater: "Lan", kind: "human", scores, notes: "Ổn",
        findings: [{ claim: "Câu bịa", field: "example", reason: "Nguồn không có" }],
        quiz_flaws: [{ question: 1, flaws: [] }, { question: 2, flaws: ["vague_terms", "absolute_terms"] }],
      }] },
      { id: "i2", anon_code: "M02", group: "llm_no_rag", pair_code: "M01", topic: "Chủ đề", criteria: ["accuracy", "completeness"], ratings: [] },
    ],
  });
  vi.mocked(api.downloadStudyFile).mockResolvedValue(new Blob(["anon_id"]));
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
  const user = userEvent.setup();
  render(<MemoryRouter><AdminStudiesPanel /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  const results = await screen.findByRole("region", { name: "Kết quả chấm" });
  const progress = within(within(results).getByRole("list", { name: "Tiến độ người chấm" }));
  // By name: the admin is not blind to who rated.
  expect(progress.getByText("Lan")).toBeInTheDocument();
  expect(progress.getByText("minh@example.com, chưa chấm gói nào")).toBeInTheDocument();
  // Only a rater with module ratings has a module file to download.
  expect(within(results).getAllByRole("button", { name: "Tải CSV mô-đun" })).toHaveLength(1);
  expect(within(results).getByLabelText("Tiến độ lan@example.com")).toHaveAttribute("value", "1");
  // Only packets someone rated are listed; the group is the admin's to see.
  expect(within(results).queryByText("M02")).not.toBeInTheDocument();
  // Opened by its arrow, not the whole row: one table with each criterion's score and comment.
  expect(within(results).queryByRole("table")).not.toBeInTheDocument();
  await user.click(within(results).getByRole("button", { name: "Xem M01" }));
  expect(within(results).getByText("RAG")).toBeVisible();
  const table = within(within(results).getByRole("table"));
  expect(table.getByRole("rowheader", { name: "Độ chính xác" })).toBeVisible();
  expect(table.getByText("Lý do accuracy")).toBeVisible();
  expect(table.getByText("Câu bịa")).toBeVisible();
  expect(table.getByText("Ví dụ")).toBeVisible();
  expect(table.getByRole("row", { name: /Ghi chú Ổn/ })).toBeVisible();
  // The last question is the practice one; a question without flaws is not listed.
  expect(table.getByRole("row", { name: /Lỗi câu hỏi Bài luyện tập\s*Phương án dùng từ mơ hồ/ })).toBeVisible();
  expect(table.queryByText("Câu 1")).not.toBeInTheDocument();
  await user.click(within(results).getByRole("button", { name: "Tải CSV mô-đun" }));
  expect(api.downloadStudyFile).toHaveBeenCalledWith("s1", "raters/1.csv");
  await user.click(within(results).getByRole("button", { name: "Tải CSV flashcard và video" }));
  expect(api.downloadStudyFile).toHaveBeenCalledWith("s1", "media.csv");
});

const summary = (id: string, code: string, group: api.StudyGroup, pair: string | null) => ({
  id, anon_code: code, group, module_id: group === "rag_llm" ? pair : null, pair_id: pair,
  reference_file_id: null, packet: { topic: `Chủ đề ${code}` },
});
const lecture = { id: "f1", filename: "Bài giảng 3.pdf", size: 1258291, created_at: "" };

async function openStudy() {
  render(<MemoryRouter><AdminStudiesPanel /></MemoryRouter>);
  await userEvent.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  return screen.findByRole("region", { name: "Nội dung chấm" });
}
async function openAddDialog(tab?: string) {
  await userEvent.click(await screen.findByRole("button", { name: "Thêm nội dung" }));
  const dialog = await screen.findByRole("dialog", { name: "Thêm nội dung" });
  if (tab) await userEvent.click(within(dialog).getByRole("tab", { name: tab }));
  return dialog;
}

it("uploads a reference file, shares one picker across a pair, and confirms before deleting and clearing pickers", async () => {
  vi.mocked(api.getStudyItems).mockResolvedValue([
    summary("a", "M01", "rag_llm", "p1"), summary("b", "M04", "llm_no_rag", "p1"), summary("c", "M02", "human", null),
  ]);
  vi.mocked(api.uploadStudyFile).mockResolvedValue(lecture);
  vi.mocked(api.setItemReference).mockResolvedValue([
    { id: "a", reference_file_id: "f1" }, { id: "b", reference_file_id: "f1" }, { id: "c", reference_file_id: null },
  ]);
  vi.mocked(api.deleteStudyFile).mockResolvedValue();
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  try {
    const region = await openStudy();
    expect(within(region).queryByRole("list", { name: "Tệp tài liệu gốc" })).not.toBeInTheDocument();
    const file = new File(["%PDF"], "Bài giảng 3.pdf", { type: "application/pdf" });

    await userEvent.upload(within(region).getByLabelText("Tải tệp lên"), file);
    expect(api.uploadStudyFile).toHaveBeenCalledWith("s1", file, expect.any(Function));
    expect(await within(region).findByRole("button", { name: "Tải Bài giảng 3.pdf" })).toBeInTheDocument();
    expect(within(region).getByText("1,2 MB")).toBeInTheDocument();

    const pair = within(region).getByRole("combobox", { name: "Tài liệu gốc của M01, M04" });
    expect(within(pair).getByRole("option", { name: "Bài giảng 3.pdf" })).toBeInTheDocument();
    expect(within(region).getByRole("combobox", { name: "Tài liệu gốc của M02" })).toHaveValue("");

    await userEvent.selectOptions(pair, "f1");
    expect(api.setItemReference).toHaveBeenCalledWith("s1", "a", "f1");
    await waitFor(() => expect(pair).toHaveValue("f1"));
    expect(within(region).getByRole("combobox", { name: "Tài liệu gốc của M02" })).toHaveValue("");

    const remove = within(region).getByRole("button", { name: "Xóa Bài giảng 3.pdf" });
    await userEvent.click(remove);
    expect(api.deleteStudyFile).not.toHaveBeenCalled();

    await userEvent.click(remove);
    expect(api.deleteStudyFile).toHaveBeenCalledWith("s1", "f1");
    await waitFor(() => expect(pair).toHaveValue(""));
    expect(within(region).queryByRole("list", { name: "Tệp tài liệu gốc" })).not.toBeInTheDocument();
  } finally {
    confirm.mockRestore();
  }
});

it("adds ready/none module pairs and Video/Flashcard items within the same dialog", async () => {
  vi.mocked(api.listPairCandidates).mockResolvedValue([
    { id: "m1", title: "Tiến trình", course_name: "Hệ điều hành", state: "ready" },
    { id: "m2", title: "Luồng", course_name: "Hệ điều hành", state: "none" },
  ]);
  vi.mocked(api.addPair).mockResolvedValueOnce("added").mockResolvedValueOnce("generating");
  const video = { id: "v1", kind: "video" as const, title: "Bài 1.pdf", topic: "Toàn bộ tài liệu", course_name: "Hệ điều hành", state: "in_study" as const };
  const cards = { id: "f1", kind: "flashcards" as const, title: "Bài 1.pdf", topic: "Định nghĩa", course_name: "Hệ điều hành" };
  // The list is read again once something is added.
  vi.mocked(api.listMediaCandidates).mockResolvedValue([{ ...cards, state: "ready" }, video]);
  vi.mocked(api.addMedia).mockResolvedValue([]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  try {
    await openStudy();
    const dialog = await openAddDialog();
    const rows = await within(within(dialog).getByRole("list", { name: "Mô-đun có thể thêm" })).findAllByRole("listitem");

    await userEvent.click(within(rows[0]).getByRole("button", { name: "Thêm" }));
    await waitFor(() => expect(api.addPair).toHaveBeenCalledWith("s1", "m1"));
    // A twin already made costs nothing, so nothing is asked.
    expect(confirm).not.toHaveBeenCalled();

    await userEvent.click(within(rows[1]).getByRole("button", { name: "Thêm" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(api.addPair).toHaveBeenLastCalledWith("s1", "m2"));

    await userEvent.click(within(dialog).getByRole("tab", { name: "Video" }));
    expect(within(within(dialog).getByRole("list", { name: "Video có thể thêm" })).getByText("Đã thêm")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("tab", { name: "Flashcard" }));
    const flashcards = within(dialog).getByRole("list", { name: "Flashcard có thể thêm" });

    vi.mocked(api.listMediaCandidates).mockResolvedValue([{ ...cards, state: "in_study" }, video]);
    await userEvent.click(within(flashcards).getByRole("button", { name: "Thêm" }));
    await waitFor(() => expect(api.addMedia).toHaveBeenCalledWith("s1", "flashcards", "f1"));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(await within(flashcards).findByText("Đã thêm")).toBeInTheDocument();
  } finally {
    confirm.mockRestore();
  }
});

it("gives each row to the accounts picked for it, so different rows go to different people", async () => {
  vi.mocked(api.getRaters).mockResolvedValue({ emails: ["minh@example.com"], assignments: { "minh@example.com": ["p2"] } });
  vi.mocked(api.getStudyItems).mockResolvedValue([
    summary("a", "M01", "rag_llm", "p1"), summary("b", "M04", "llm_no_rag", "p1"),
    summary("c", "M02", "rag_llm", "p2"), summary("d", "M03", "llm_no_rag", "p2"),
  ]);
  vi.mocked(api.setRaters).mockImplementation(async (_s, emails, assignments) => ({ emails, assignments: assignments ?? {} }));
  const card = await openStudy();
  const rows = within(card).getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("Chưa giao")).toBeInTheDocument();
  // By name, not a count.
  expect(within(rows[1]).getByText("Minh Trần")).toBeInTheDocument();

  // A row with raters changes them from its menu; one without has "Giao" in view.
  expect(within(rows[1]).queryByRole("button", { name: /Giao người chấm/ })).not.toBeInTheDocument();
  within(rows[1]).getByRole("button", { name: "Thao tác khác với M02, M03" }).focus();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("menuitem", { name: "Sửa người chấm" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(within(rows[0]).getByRole("button", { name: "Giao người chấm M01, M04" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getAllByRole("checkbox").every((box) => !(box as HTMLInputElement).checked)).toBe(true);
  await userEvent.type(within(dialog).getByRole("searchbox", { name: "Tìm người chấm" }), "lan");
  await userEvent.click(within(dialog).getByRole("checkbox", { name: /Lan/ }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));

  // Lan gets this row only; Minh keeps the other one.
  await waitFor(() => expect(api.setRaters).toHaveBeenCalledWith("s1", ["minh@example.com", "lan@example.com"],
    { "minh@example.com": ["p2"], "lan@example.com": ["p1"] }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(within(rows[0]).getByText("Lan")).toBeInTheDocument();
  expect(within(rows[1]).queryByText("Lan")).not.toBeInTheDocument();
});

it("asks before taking a pair out of the study, then lists what is left", async () => {
  vi.mocked(api.getStudyItems).mockResolvedValueOnce([summary("a", "M01", "rag_llm", "p1"), summary("b", "M04", "llm_no_rag", "p1")])
    .mockResolvedValue([]);
  vi.mocked(api.removeStudyItem).mockResolvedValue([]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  try {
    const card = await openStudy();
    async function remove() {
      within(card).getByRole("button", { name: "Thao tác khác với M01, M04" }).focus();
      await userEvent.keyboard("{Enter}");
      // A module keeps its name: only a flashcard set or video can be renamed.
      expect(screen.queryByRole("menuitem", { name: "Đổi tên" })).not.toBeInTheDocument();
      await userEvent.click(await screen.findByRole("menuitem", { name: "Xóa nghiên cứu" }));
    }
    await remove();
    expect(api.removeStudyItem).not.toHaveBeenCalled();

    await remove();

    expect(api.removeStudyItem).toHaveBeenCalledWith("s1", "a");
    expect(await within(card).findByText("Chưa có nội dung nào.")).toBeInTheDocument();
  } finally {
    confirm.mockRestore();
  }
});

it("lists a pair as one row alongside a flashcard row and renames the flashcard row from its menu", async () => {
  const pairItems = [
    { ...summary("a", "M01", "rag_llm", "p1"), source: { title: "Tiến trình", course_name: "HĐH", created_at: "2026-11-19T10:00:00Z" } },
    { ...summary("b", "M02", "llm_no_rag", "p1"), source: { title: "Tiến trình", course_name: "HĐH", created_at: "2026-11-19T10:00:00Z" } },
  ];
  vi.mocked(api.getStudyItems).mockResolvedValue([
    ...pairItems,
    { ...summary("c", "F01", "flashcards", null), packet: { topic: "Toàn bộ tài liệu" },
      source: { title: "Bài 1.pdf", course_name: "HĐH", created_at: "2026-11-20T10:00:00Z" } },
  ]);
  vi.mocked(api.renameStudyItem).mockResolvedValue([
    ...pairItems,
    { ...summary("c", "F01", "flashcards", null), packet: { topic: "Định nghĩa token" },
      source: { title: "Bài 1.pdf", course_name: "HĐH", created_at: "2026-11-20T10:00:00Z" } },
  ]);

  const card = await openStudy();
  const rows = within(card).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByText("Tiến trình")).toBeInTheDocument();
  expect(within(rows[0]).getByText(/Có và không dùng tài liệu/)).toBeInTheDocument();
  expect(within(rows[1]).getByText("Flashcard")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Toàn bộ tài liệu")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Bài 1.pdf")).toBeInTheDocument();

  within(card).getByRole("button", { name: "Thao tác khác với F01" }).focus();
  await userEvent.keyboard("{Enter}");
  await userEvent.click(await screen.findByRole("menuitem", { name: "Đổi tên" }));
  const field = within(card).getByRole("textbox", { name: "Tên hiển thị cho người chấm" });
  await userEvent.clear(field);
  await userEvent.type(field, "Định nghĩa token{Enter}");
  expect(api.renameStudyItem).toHaveBeenCalledWith("s1", "c", "Định nghĩa token");
  expect(await within(card).findByText("Định nghĩa token")).toBeInTheDocument();
});

it("opens the study named in the address, so a reload stays on it", async () => {
  render(<MemoryRouter initialEntries={["/admin?tab=studies&study=s1"]}><AdminStudiesPanel /></MemoryRouter>);
  expect(await screen.findByRole("region", { name: "Nội dung chấm" })).toBeInTheDocument();
  expect(api.getRaters).toHaveBeenCalledWith("s1");
});

it("falls back to the sole study when the address names a closed study and hides the study nav list", async () => {
  vi.mocked(api.listStudies).mockResolvedValue([study]);
  render(<MemoryRouter initialEntries={["/admin?tab=studies&study=closed"]}><AdminStudiesPanel /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Nghiên cứu" })).toBeInTheDocument();
  expect(await screen.findByRole("region", { name: "Nội dung chấm" })).toBeInTheDocument();
  await waitFor(() => expect(api.getRaters).toHaveBeenCalledWith("s1"));
  expect(api.getRaters).not.toHaveBeenCalledWith("closed");
  expect(screen.queryByRole("navigation", { name: "Nghiên cứu" })).not.toBeInTheDocument();
});

it("rates a flashcard set on its own four criteria and sends them as scores", async () => {
  const cards = {
    id: "i9", anon_code: "F01", kind: "flashcards" as const, rating: null, reference: null,
    packet: { kind: "flashcards" as const, anon_id: "F01", topic: "Định nghĩa", cards: [{ front: "Tiến trình là gì?", back: "Chương trình đang chạy." }] },
  };
  vi.mocked(api.myStudyItems).mockResolvedValue({ ...data, items: [cards] });
  vi.mocked(api.saveRating).mockImplementation(async (_s, _i, body) => body);
  render(
    <MemoryRouter initialEntries={["/studies/s1/items/i9"]}>
      <Routes><Route path="/studies/:studyId/items/:itemId" element={<BlindRatingPage />} /></Routes>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  expect(await screen.findByText("Tiến trình là gì?")).toBeInTheDocument();
  expect(screen.queryByText("Độ khớp mức Bloom")).not.toBeInTheDocument();
  for (const label of ["Độ chính xác", "Trọng tâm", "Rõ ràng", "Không trùng lặp"]) {
    const summary = screen.getByText(new RegExp(`\\d\\. ${label}$`));
    if (!summary.closest("details")?.open) await user.click(summary);
    const group = screen.getByRole("radiogroup", { name: `Điểm ${label}` });
    await user.click(within(group).getByText("4"));
    await user.type(screen.getByLabelText(new RegExp(`Nhận xét\\s+${label}`)), "Ổn");
  }
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  await waitFor(() => expect(api.saveRating).toHaveBeenCalled());
  const body = vi.mocked(api.saveRating).mock.calls[0][2] as { scores: Record<string, { score: number }> };
  expect(Object.keys(body.scores)).toEqual(["accuracy", "focus", "clarity", "no_duplicates"]);
  expect(body.scores.clarity.score).toBe(4);
});

it("shows video packet tabs, recovers failed video renewal limit, and handles study/item errors", async () => {
  vi.mocked(api.myStudyItems).mockRejectedValueOnce(new Error("forbidden"));
  const first = render(
    <MemoryRouter initialEntries={["/studies/s1"]}>
      <Routes><Route path="/studies/:studyId" element={<BlindRatingPage />} /></Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Không tải được nghiên cứu");
  expect(screen.queryByRole("button", { name: "Lưu đánh giá" })).not.toBeInTheDocument();
  first.unmount();

  const videoWithMedia = {
    id: "v-item", anon_code: "V01", kind: "video" as const, rating: null, reference: null,
    packet: {
      kind: "video" as const, anon_id: "V01", topic: "Thuật toán sắp xếp", has_video: true,
      scenes: [{ title: "Mở đầu", bullets: ["Ý 1"], narration: "Lời thoại cảnh 1" }],
    },
  };
  vi.mocked(api.myStudyItems).mockResolvedValue({ ...data, items: [videoWithMedia], total: 1 });
  vi.mocked(api.getPacketVideo).mockResolvedValue({ url: "https://s3.example/video.mp4", via_api: false, filename: "v.mp4" });
  const videoView = render(
    <MemoryRouter initialEntries={["/studies/s1/items/v-item"]}>
      <Routes><Route path="/studies/:studyId/items/:itemId" element={<BlindRatingPage />} /></Routes>
    </MemoryRouter>,
  );
  const player = await screen.findByLabelText("Video", { selector: "video" });
  await waitFor(() => expect(player).toHaveAttribute("src", "https://s3.example/video.mp4"));
  await userEvent.click(screen.getByRole("tab", { name: "Các cảnh" }));
  expect(screen.getByText("1. Mở đầu")).toBeInTheDocument();
  expect(screen.getByText("Lời thoại cảnh 1")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("tab", { name: "Video" }));
  const remountedPlayer = await screen.findByLabelText("Video", { selector: "video" });
  await waitFor(() => expect(remountedPlayer).toHaveAttribute("src", "https://s3.example/video.mp4"));
  vi.mocked(api.getPacketVideo).mockClear();
  fireEvent.error(remountedPlayer);
  await waitFor(() => expect(api.getPacketVideo).toHaveBeenCalledTimes(1));
  fireEvent.error(screen.getByLabelText("Video", { selector: "video" }));
  await waitFor(() => expect(api.getPacketVideo).toHaveBeenCalledTimes(2));
  fireEvent.error(screen.getByLabelText("Video", { selector: "video" }));
  expect(await screen.findByText("Không tải được video. Hãy tải lại trang.")).toBeInTheDocument();
  videoView.unmount();

  vi.mocked(api.myStudyItems).mockResolvedValue({ ...data, items: [videoWithMedia], total: 1 });
  render(
    <MemoryRouter initialEntries={["/studies/s1/items/does-not-exist"]}>
      <Routes><Route path="/studies/:studyId/items/:itemId" element={<BlindRatingPage />} /></Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Không tìm thấy gói chấm.");
});
