import { render, screen, waitFor, within } from "@testing-library/react";
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
const noResults = { criteria: [], total: 0, raters: [], has_judge: false, unpaired: [], items: [] };
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
  vi.mocked(api.listStudies).mockResolvedValue([study]);
  vi.mocked(api.getRaters).mockResolvedValue({ emails: ["lan@example.com"], assignments: {} });
  vi.mocked(api.listPairCandidates).mockResolvedValue([]);
  vi.mocked(api.getStudyResults).mockResolvedValue(noResults);
  vi.mocked(api.listStudyFiles).mockResolvedValue([]);
  vi.mocked(api.getStudyItems).mockResolvedValue([]);
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
  expect(await screen.findByText("Nguồn đối chiếu")).toBeInTheDocument();
  for (const [tab, text] of [["Mục tiêu", "Mục tiêu con"], ["Nội dung", "Nội dung bài"], ["Slide", "Ý chính"], ["Ví dụ", "Bối cảnh"], ["Luyện tập", "Câu hỏi"], ["Quiz", "Câu hỏi"]]) {
    await user.click(screen.getByRole("tab", { name: tab }));
    expect(screen.getByText(text)).toBeInTheDocument();
  }
  await user.click(screen.getByRole("tab", { name: "Giải thích đáp án" }));
  expect(screen.getAllByText("Lời giải")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  expect(api.saveRating).not.toHaveBeenCalled();
  for (const group of screen.getAllByRole("radiogroup", { name: /Điểm/ }))
    await user.click(within(group).getByRole("radio", { name: "4" }));
  for (const field of screen.getAllByRole("textbox", { name: /Nhận xét/ }))
    await user.type(field, "Có căn cứ");
  await user.click(screen.getByText("Ghi chú chung (tùy chọn)"));
  await user.type(screen.getByLabelText("Ghi chú tổng quan"), "Tổng quan");
  expect(screen.getByRole("heading", { name: "Khẳng định không có căn cứ (0)" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Thêm khẳng định không có căn cứ" }));
  expect(screen.getByRole("heading", { name: "Khẳng định không có căn cứ (1)" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Khẳng định 1"), "Khẳng định");
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
it("keeps a criterion opened by a failed save open while its comment is typed", async () => {
  const user = userEvent.setup();
  blind();
  const criterion = (await screen.findByText(/3\. Tính nhất quán/)).closest("details")!;
  expect(criterion).not.toHaveAttribute("open");
  await user.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  await waitFor(() => expect(criterion).toHaveAttribute("open"));
  await user.click(within(criterion).getByRole("radio", { name: "4" }));
  await user.type(within(criterion).getByRole("textbox", { name: /Nhận xét/ }), "C");
  await waitFor(() => expect(within(criterion).queryByRole("alert")).not.toBeInTheDocument());
  expect(criterion).toHaveAttribute("open");
  expect(within(criterion).getByText("Câu hỏi, lời giải và ví dụ có khớp nhau không?")).toBeInTheDocument();
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
  await screen.findByText("Nguồn đối chiếu");
  await userEvent.click(screen.getByText("Ghi chú chung (tùy chọn)"));
  await userEvent.type(screen.getByLabelText("Ghi chú tổng quan"), " mới");
  await userEvent.click(screen.getByRole("button", { name: "Lưu đánh giá" }));
  expect(await screen.findByText("Không lưu được đánh giá.")).toBeInTheDocument();
  expect(screen.getByLabelText("Ghi chú tổng quan")).toHaveValue("Cũ mới");
});
it("asks before a link or logout discards unsaved input, and not once it is saved", async () => {
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
    overall_notes: "Cũ",
    hallucination_findings: [],
  };
  vi.mocked(api.myStudyItems).mockResolvedValue({
    ...data,
    rated: 1,
    items: [{ ...item, rating }],
  });
  vi.mocked(api.saveRating).mockImplementation(async (_s, _i, body) => body);
  render(
    <MemoryRouter initialEntries={["/studies/s1/items/i1"]}>
      <Routes>
        <Route path="/studies/:studyId/items/:itemId" element={<BlindRatingPage />} />
        <Route path="/studies" element={<p>Danh sách</p>} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText("Nguồn đối chiếu");
  await user.click(screen.getByText("Ghi chú chung (tùy chọn)"));
  await user.type(screen.getByLabelText("Ghi chú tổng quan"), " mới");
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
});
it("reports missing or revoked invitations", async () => {
  vi.mocked(api.myStudyItems).mockRejectedValue(new Error("404"));
  blind();
  expect(await screen.findByRole("alert")).toHaveTextContent("Không tải được nghiên cứu");
  expect(screen.queryByRole("button", { name: "Lưu đánh giá" })).not.toBeInTheDocument();
});
it("admin edits invited emails and can remove all raters", async () => {
  vi.mocked(api.setRaters).mockImplementation(async (_s, emails) => ({ emails, assignments: {} }));
  render(<AdminStudiesPanel />);
  await userEvent.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  const input = await screen.findByLabelText("Người chấm");
  await screen.findByRole("button", { name: "Bỏ lan@example.com" });
  await userEvent.type(input, "bad{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("Email không hợp lệ");
  expect(api.setRaters).not.toHaveBeenCalled();
  await userEvent.clear(input);
  await userEvent.type(input, "minh@example.com{Enter}");
  await screen.findByRole("button", { name: "Bỏ minh@example.com" });
  await userEvent.click(screen.getByRole("button", { name: /Nghiên cứu/ }));
  expect(screen.getByLabelText("Người chấm")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Lưu người chấm" }));
  await waitFor(() => expect(api.setRaters).toHaveBeenCalledWith("s1", ["lan@example.com", "minh@example.com"]));
  await userEvent.click(screen.getByRole("button", { name: "Bỏ lan@example.com" }));
  await userEvent.click(screen.getByRole("button", { name: "Bỏ minh@example.com" }));
  await userEvent.click(screen.getByRole("button", { name: "Lưu người chấm" }));
  await waitFor(() => expect(api.setRaters).toHaveBeenLastCalledWith("s1", []));
});

it("saves a typed email that was not yet added with Enter", async () => {
  vi.mocked(api.setRaters).mockImplementation(async (_s, emails) => ({ emails, assignments: {} }));
  render(<AdminStudiesPanel />);
  await userEvent.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  await userEvent.type(await screen.findByLabelText("Người chấm"), "Minh@Example.com");
  await userEvent.click(screen.getByRole("button", { name: "Lưu người chấm" }));
  await waitFor(() => expect(api.setRaters).toHaveBeenCalledWith("s1", ["lan@example.com", "minh@example.com"]));
  expect(await screen.findByText("Đã lưu người chấm.")).toBeInTheDocument();
});

it("adds a pasted list of emails as chips without duplicates", async () => {
  render(<AdminStudiesPanel />);
  await userEvent.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  const input = await screen.findByLabelText("Người chấm");
  await screen.findByRole("button", { name: "Bỏ lan@example.com" });
  input.focus();
  await userEvent.paste("Lan@example.com\nminh@example.com, an@example.com;minh@example.com");
  expect(screen.getAllByRole("button", { name: /^Bỏ / }).map((b) => b.getAttribute("aria-label"))).toEqual([
    "Bỏ lan@example.com", "Bỏ minh@example.com", "Bỏ an@example.com",
  ]);
  expect(input).toHaveValue("");
});

it("keeps a pasted list with a bad email in the field and clears the error when a chip is removed", async () => {
  render(<AdminStudiesPanel />);
  await userEvent.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  const input = await screen.findByLabelText("Người chấm");
  await screen.findByRole("button", { name: "Bỏ lan@example.com" });
  input.focus();
  await userEvent.paste("minh@example.com\nbad");
  expect(await screen.findByRole("alert")).toHaveTextContent("Email không hợp lệ");
  expect(screen.queryByRole("button", { name: "Bỏ minh@example.com" })).not.toBeInTheDocument();
  expect(input).toHaveValue("minh@example.com bad");
  await userEvent.click(screen.getByRole("button", { name: "Bỏ lan@example.com" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("loads the raters again when the same study is picked after a failed load", async () => {
  vi.mocked(api.getRaters).mockRejectedValueOnce(new Error("offline"));
  render(<AdminStudiesPanel />);
  const pick = await screen.findByRole("button", { name: /Nghiên cứu/ });
  await userEvent.click(pick);
  expect(await screen.findByRole("alert")).toHaveTextContent("Không tải được người chấm.");
  await userEvent.click(pick);
  expect(await screen.findByRole("button", { name: "Bỏ lan@example.com" })).toBeInTheDocument();
  expect(api.getRaters).toHaveBeenCalledTimes(2);
});

it("shows the declared Bloom level only beside the bloom_fidelity criterion", async () => {
  blind();
  const criterion = (await screen.findByText(/7\. Độ khớp mức Bloom/)).closest("details")!;
  expect(within(criterion).getByText("Mức Bloom đặt ra: Hiểu")).toBeInTheDocument();
  expect(screen.getAllByText(/Mức Bloom đặt ra/)).toHaveLength(1);
  for (const tab of screen.getAllByRole("tab")) {
    await userEvent.click(tab);
    expect(within(screen.getByRole("tabpanel")).queryByText(/Mức Bloom|Hiểu|understand/)).not.toBeInTheDocument();
  }
});

it("shows the study and the anonymous code in the breadcrumb", async () => {
  blind();
  const trail = await screen.findByRole("navigation", { name: "Đường dẫn" });
  expect(trail).toHaveTextContent("Nghiên cứu / Nghiên cứu / M01");
});

it("counts the criteria still missing a score or a comment beside the save button", async () => {
  const user = userEvent.setup();
  blind();
  expect(await screen.findByText("Còn 8 tiêu chí")).toBeInTheDocument();
  const accuracy = screen.getByRole("radiogroup", { name: "Điểm Độ chính xác" });
  await user.click(within(accuracy).getByRole("radio", { name: "5" }));
  expect(screen.getByText("Còn 8 tiêu chí")).toBeInTheDocument();
  await user.type(screen.getByRole("textbox", { name: "Nhận xét Độ chính xác" }), "Khớp nguồn");
  expect(screen.getByText("Còn 7 tiêu chí")).toBeInTheDocument();
});

it("keeps the correct answer out of the Quiz tab and shows it under Giải thích đáp án", async () => {
  const user = userEvent.setup();
  blind();
  await user.click(await screen.findByRole("tab", { name: "Quiz" }));
  expect(within(screen.getByRole("tabpanel")).getByText("Câu hỏi")).toBeInTheDocument();
  expect(screen.queryByText(/Đáp án đúng/)).not.toBeInTheDocument();
  expect(screen.queryByText("Lời giải")).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Giải thích đáp án" }));
  expect(screen.getAllByText(/Đáp án đúng/)).toHaveLength(2);
});

it("opens sources first and switches packet tabs with arrow keys", async () => {
  blind();
  expect(await screen.findByText("Nguồn đối chiếu")).toBeVisible();
  expect(screen.queryByText("Nội dung bài")).not.toBeInTheDocument();
  screen.getByRole("tab", { name: /Đoạn nguồn/ }).focus();
  await userEvent.keyboard("{ArrowRight}{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Nội dung" })).toHaveFocus();
  expect(screen.getByText("Nội dung bài")).toBeVisible();
  expect(screen.queryByText("Nguồn đối chiếu")).not.toBeInTheDocument();
});

it("shows only the packet picked when moving between packets and back", async () => {
  const second = {
    ...item,
    id: "i2",
    anon_code: "M02",
    packet: { ...item.packet, anon_id: "M02", source_chunks: ["Nguồn của gói hai"] },
  };
  vi.mocked(api.myStudyItems).mockResolvedValue({ ...data, items: [item, second], total: 2 });
  const user = userEvent.setup();
  blind();
  await screen.findByText("Nguồn đối chiếu");
  const packets = within(screen.getByRole("navigation", { name: "Gói chấm" }));
  await user.click(packets.getByRole("link", { name: /M02/ }));
  await screen.findByText("Nguồn của gói hai");
  await user.click(packets.getByRole("link", { name: /M01/ }));
  await screen.findByText("Nguồn đối chiếu");
  // The packet left behind must be gone, not kept beside the one now rated.
  expect(screen.queryByText("Nguồn của gói hai")).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(1);
});

it("offers every part of the module that can hold an unsupported claim, but not the objective", async () => {
  const user = userEvent.setup();
  blind();
  await screen.findByText("Nguồn đối chiếu");
  await user.click(screen.getByRole("button", { name: /Thêm/ }));
  const location = screen.getByRole("combobox", { name: /Vị trí 1/ });
  expect(within(location).getAllByRole("option").map((o) => o.textContent)).toEqual(
    ["Nội dung", "Slide", "Ví dụ", "Quiz", "Bài luyện tập", "Lời giải thích"],
  );
});

it("shows the admin each rater's progress, and per packet the scores, findings and group", async () => {
  const scores = Object.fromEntries(["accuracy", "completeness"].map((c) => [c, { score: 4, justification: `Lý do ${c}` }]));
  vi.mocked(api.getStudyResults).mockResolvedValue({
    criteria: ["accuracy", "completeness"], total: 2, has_judge: false, unpaired: [],
    raters: [{ email: "lan@example.com", number: 1, rated: 1, invited: true, assigned: 2 }, { email: "minh@example.com", number: null, rated: 0, invited: true, assigned: 2 }],
    items: [
      { id: "i1", anon_code: "M01", group: "rag_llm", pair_code: "M01", topic: "Chủ đề", ratings: [{
        rater: "Người chấm 1", kind: "human", scores, notes: "Ổn",
        findings: [{ claim: "Câu bịa", field: "example", reason: "Nguồn không có" }],
      }] },
      { id: "i2", anon_code: "M02", group: "llm_no_rag", pair_code: "M01", topic: "Chủ đề", ratings: [] },
    ],
  });
  vi.mocked(api.downloadStudyFile).mockResolvedValue(new Blob(["anon_id"]));
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
  const user = userEvent.setup();
  render(<AdminStudiesPanel />);
  await user.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  const results = await screen.findByRole("region", { name: "Kết quả chấm" });
  const progress = within(within(results).getByRole("list", { name: "Tiến độ người chấm" }));
  expect(progress.getByText("Người chấm 1")).toBeInTheDocument();
  expect(progress.getByText("Chưa chấm gói nào")).toBeInTheDocument();
  expect(within(results).getByLabelText("Tiến độ lan@example.com")).toHaveAttribute("value", "1");
  // Only packets someone rated are listed; the group is the admin's to see.
  expect(within(results).queryByText("M02")).not.toBeInTheDocument();
  await user.click(within(results).getByText("M01"));
  expect(within(results).getByText("RAG")).toBeVisible();
  expect(within(results).getByRole("cell", { name: "Độ chính xác" })).toBeVisible();
  expect(within(results).getByText("Câu bịa")).toBeVisible();
  expect(within(results).getByText("Ví dụ")).toBeVisible();
  await user.click(within(results).getByRole("button", { name: "Tải CSV" }));
  expect(api.downloadStudyFile).toHaveBeenCalledWith("s1", "raters/1.csv");
});

const summary = (id: string, code: string, group: "rag_llm" | "llm_no_rag" | "human", pair: string | null) => ({
  id, anon_code: code, group, module_id: group === "rag_llm" ? pair : null, pair_id: pair,
  reference_file_id: null, packet: { topic: `Chủ đề ${code}` },
});
const lecture = { id: "f1", filename: "Bài giảng 3.pdf", size: 1258291, created_at: "" };

async function openStudy() {
  render(<AdminStudiesPanel />);
  await userEvent.click(await screen.findByRole("button", { name: /Nghiên cứu/ }));
  return screen.findByRole("region", { name: "Tài liệu gốc" });
}

it("gives a pair one file picker, so both packets always share the file", async () => {
  vi.mocked(api.getStudyItems).mockResolvedValue([
    summary("a", "M01", "rag_llm", "p1"), summary("b", "M04", "llm_no_rag", "p1"), summary("c", "M02", "human", null),
  ]);
  vi.mocked(api.listStudyFiles).mockResolvedValue([lecture]);
  vi.mocked(api.setItemReference).mockResolvedValue([
    { id: "a", reference_file_id: "f1" }, { id: "b", reference_file_id: "f1" }, { id: "c", reference_file_id: null },
  ]);
  const region = await openStudy();
  expect(within(region).getByText("1,2 MB")).toBeInTheDocument();
  const pair = within(region).getByRole("combobox", { name: "Tài liệu gốc của M01, M04" });
  expect(within(region).getByRole("combobox", { name: "Tài liệu gốc của M02" })).toHaveValue("");

  await userEvent.selectOptions(pair, "f1");

  expect(api.setItemReference).toHaveBeenCalledWith("s1", "a", "f1");
  await waitFor(() => expect(pair).toHaveValue("f1"));
  expect(within(region).getByRole("combobox", { name: "Tài liệu gốc của M02" })).toHaveValue("");
});

it("uploads a file to the study and offers it to every packet", async () => {
  vi.mocked(api.getStudyItems).mockResolvedValue([summary("c", "M02", "human", null)]);
  vi.mocked(api.uploadStudyFile).mockResolvedValue(lecture);
  const region = await openStudy();
  expect(within(region).getByText("Chưa có tệp nào.")).toBeInTheDocument();
  const file = new File(["%PDF"], "Bài giảng 3.pdf", { type: "application/pdf" });

  await userEvent.upload(within(region).getByLabelText("Tải tệp lên"), file);

  expect(api.uploadStudyFile).toHaveBeenCalledWith("s1", file, expect.any(Function));
  expect(await within(region).findByRole("button", { name: "Tải Bài giảng 3.pdf" })).toBeInTheDocument();
  expect(within(region).getByRole("option", { name: "Bài giảng 3.pdf" })).toBeInTheDocument();
});

it("asks before deleting a file and clears it from the packets that used it", async () => {
  vi.mocked(api.getStudyItems).mockResolvedValue([{ ...summary("c", "M02", "human", null), reference_file_id: "f1" }]);
  vi.mocked(api.listStudyFiles).mockResolvedValue([lecture]);
  vi.mocked(api.deleteStudyFile).mockResolvedValue();
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  try {
    const region = await openStudy();
    const remove = within(region).getByRole("button", { name: "Xóa Bài giảng 3.pdf" });
    await userEvent.click(remove);
    expect(api.deleteStudyFile).not.toHaveBeenCalled();

    await userEvent.click(remove);

    expect(api.deleteStudyFile).toHaveBeenCalledWith("s1", "f1");
    await waitFor(() => expect(within(region).getByRole("combobox", { name: "Tài liệu gốc của M02" })).toHaveValue(""));
    expect(within(region).getByText("Chưa có tệp nào.")).toBeInTheDocument();
  } finally {
    confirm.mockRestore();
  }
});

it("offers the rater the packet's own file, and nothing when there is none", async () => {
  vi.mocked(api.myStudyItems).mockResolvedValue({ ...data, items: [
    { ...item, reference: { filename: "Bài giảng 3.pdf", size: 1258291 } },
    { ...item, id: "i2", anon_code: "M02" },
  ] });
  vi.mocked(api.downloadPacketReference).mockResolvedValue();
  blind();
  expect(await screen.findByText("Bài giảng 3.pdf")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Tải xuống" }));

  expect(api.downloadPacketReference).toHaveBeenCalledWith("s1", "i1");
  await userEvent.click(screen.getByRole("link", { name: "M02" }));
  await waitFor(() => expect(screen.queryByText("Bài giảng 3.pdf")).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Tải xuống" })).not.toBeInTheDocument();
});

it("adds a pair whose no-RAG twin is made, and asks before making one", async () => {
  vi.mocked(api.listPairCandidates).mockResolvedValue([
    { id: "m1", title: "Tiến trình", course_name: "Hệ điều hành", state: "ready" },
    { id: "m2", title: "Luồng", course_name: "Hệ điều hành", state: "none" },
  ]);
  vi.mocked(api.addPair).mockResolvedValueOnce("added").mockResolvedValueOnce("generating");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  await openStudy();
  const card = await screen.findByRole("region", { name: "Thêm cặp từ mô-đun" });
  const rows = await within(card).findAllByRole("listitem");

  await userEvent.click(within(rows[0]).getByRole("button", { name: "Thêm cặp" }));
  await waitFor(() => expect(api.addPair).toHaveBeenCalledWith("s1", "m1"));
  // A twin already made costs nothing, so nothing is asked.
  expect(confirm).not.toHaveBeenCalled();

  await userEvent.click(within(rows[1]).getByRole("button", { name: "Thêm cặp" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(api.addPair).toHaveBeenLastCalledWith("s1", "m2"));
});

it("gives a rater only the pairs ticked, and refuses a rater left with none", async () => {
  vi.mocked(api.getRaters).mockResolvedValue({ emails: ["lan@example.com"], assignments: {} });
  vi.mocked(api.getStudyItems).mockResolvedValue([
    summary("a", "M01", "rag_llm", "p1"), summary("b", "M04", "llm_no_rag", "p1"),
    summary("c", "M02", "rag_llm", "p2"), summary("d", "M03", "llm_no_rag", "p2"),
  ]);
  vi.mocked(api.setRaters).mockImplementation(async (_s, emails, assignments) => ({ emails, assignments: assignments ?? {} }));
  await openStudy();
  const card = await screen.findByRole("region", { name: "Phân công" });

  const first = await within(card).findByRole("checkbox", { name: "lan@example.com chấm M01, M04" });
  expect(first).toBeChecked();
  await userEvent.click(within(card).getByRole("checkbox", { name: "lan@example.com chấm M02, M03" }));
  await userEvent.click(within(card).getByRole("button", { name: "Lưu phân công" }));
  await waitFor(() => expect(api.setRaters).toHaveBeenCalledWith("s1", ["lan@example.com"], { "lan@example.com": ["p1"] }));

  await userEvent.click(first);
  await userEvent.click(within(card).getByRole("button", { name: "Lưu phân công" }));
  expect(api.setRaters).toHaveBeenCalledTimes(1);
});
