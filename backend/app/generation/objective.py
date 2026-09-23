"""Learning objective, with its own check against the source."""

from pydantic import BaseModel, Field

from app.generation.common import (
    BLOOM_LEVEL_ANCHORS,
    BLOOM_VERB_TABLE,
    IGNORE_EMBEDDED_INSTRUCTIONS,
    BloomLevel,
    format_source_chunks,
)
from app.generation.planner import Concept
from app.llm.gateway import complete_structured
from app.models import Chunk

# ---------------------------------------------------------------------------
# 2. Learning Objective — Verb analysis CoT, 1 lệnh gọi duy nhất (mục 5.1)
# ---------------------------------------------------------------------------

LO_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + f"""Bạn là chuyên gia thiết kế mục tiêu học tập (Learning Objective) theo khung
Bloom's Taxonomy cho giáo dục đại học.

Nhiệm vụ: từ 1 khái niệm cốt lõi và các đoạn nguồn liên quan được cung cấp,
phân tích độ sâu của nội dung nguồn (Verb Analysis) để suy ra đúng 1 cấp độ
Bloom's phù hợp, rồi viết 1 Learning Objective hoàn chỉnh.
{BLOOM_VERB_TABLE}
Mô tả hành vi cụ thể từng cấp (dùng để QUYẾT ĐỊNH, không chỉ để tham khảo tên
động từ):
- remember: {BLOOM_LEVEL_ANCHORS["remember"]}
- understand: {BLOOM_LEVEL_ANCHORS["understand"]}
- apply: {BLOOM_LEVEL_ANCHORS["apply"]}
- analyze: {BLOOM_LEVEL_ANCHORS["analyze"]}
- evaluate: {BLOOM_LEVEL_ANCHORS["evaluate"]}
- create: {BLOOM_LEVEL_ANCHORS["create"]}

Ràng buộc BẮT BUỘC:
- Chọn bloom_level KHỚP ĐÚNG độ sâu của nguồn theo 2 CHIỀU, không chỉ 1
  chiều:
  (a) Nếu nguồn chỉ định nghĩa/liệt kê khái niệm (không giải thích sâu), CHỈ
      được chọn remember/understand, KHÔNG được tự nâng lên apply/analyze/
      evaluate/create.
  (b) NGƯỢC LẠI, nếu nguồn THỰC SỰ có so sánh/phân tích/ứng dụng/đánh giá
      (không chỉ định nghĩa suông), PHẢI chọn đúng cấp độ đó — KHÔNG được
      mặc định hạ về "understand" cho an toàn khi nguồn rõ ràng hỗ trợ cấp độ
      cao hơn. "Understand" không phải lựa chọn ngầm định — chỉ chọn khi
      nguồn thực sự chỉ dừng ở mức diễn giải, không phải khi không chắc.
- objective_text BẮT BUỘC bắt đầu bằng 1 động từ hành động ĐO LƯỜNG ĐƯỢC đúng
  cấp độ đã chọn (xem bảng động từ Bloom's ở trên), dạng "Sau khi hoàn thành
  module, người học có thể [động từ] ...".
- CẤM tuyệt đối dùng các động từ/cụm từ MƠ HỒ, KHÔNG đo lường được sau làm
  động từ hành động: "hiểu", "biết", "học", "nắm được", "nắm vững", "có kiến
  thức về", "làm quen với". Kể cả ở cấp độ understand, PHẢI dùng động từ đo
  lường được cụ thể (giải thích/mô tả/phân biệt/tóm tắt/...) — "hiểu" không
  phải 1 động từ hành động hợp lệ dù đúng cấp độ về mặt ý nghĩa.
- verb phải là động từ THỰC SỰ dùng trong objective_text.
- ATOMIC: objective_text chỉ được chứa ĐÚNG 1 mục tiêu hiệu năng (performance
  target) duy nhất — không được gộp 2 hành động khác nhau vào 1 câu bằng
  "và"/"cũng như" (vd "giải thích X và áp dụng Y" là SAI vì gộp 2 mục tiêu
  khác cấp độ nhận thức; sub_objectives mới là chỗ để tách các khía cạnh).
- Ngôn ngữ của objective_text khớp ngôn ngữ chính của nguồn (tiếng Việt có
  dấu đầy đủ nếu nguồn là tiếng Việt); riêng thuật ngữ kỹ thuật/tên riêng được
  phép giữ tiếng Anh xen kẽ.
- CHỈ dựa vào nội dung nguồn được cung cấp, không thêm kiến thức ngoài nguồn.

Cấu trúc LO đa tầng (GD2 — Terminal Objective + Enabling Objectives, theo
nguyên tắc "1 Main LO + 2-3 Sub-objectives" đã kiểm chứng qua thực tiễn
microlearning của Moodle/Raccoon Gang và Konstantly/Beedeez): objective_text
là Terminal Objective (mục tiêu tổng thể) — module vẫn CHỈ CÓ 1 module, KHÔNG
tách thành nhiều module. Ngoài ra PHẢI viết thêm 2-3 sub_objectives, mỗi cái
là 1 Enabling Objective cụ thể (dạng "[động từ] ...", KHÔNG cần lặp lại "Sau
khi hoàn thành module..."), mỗi sub-objective nhắm đúng 1 khía cạnh/năng lực
THÀNH PHẦN khác nhau cần đạt để hoàn thành Terminal Objective — không được 2
sub-objective trùng lặp cùng 1 khía cạnh. Cấp độ Bloom's của từng
sub-objective PHẢI ≤ cấp độ của objective_text (không được cao hơn mục tiêu
tổng thể nó phục vụ). Mục đích: ép Content bám đủ rộng theo từng khía cạnh
thay vì chỉ tổng hợp 1 góc hẹp của nguồn.

Ví dụ mẫu (Few-Shot CoT — 2 ví dụ đầy đủ chuỗi lập luận, KHÔNG copy nguyên
văn cho chủ đề mới, chỉ học đúng CÁCH suy luận):

Ví dụ 1 — nguồn chỉ định nghĩa/liệt kê (không giải thích sâu):
  Khái niệm: "Encapsulation". Nguồn: "Encapsulation: Bundles data and methods
  into a single unit (class) and restricts access to protect data integrity."
  Lập luận: nguồn CHỈ định nghĩa 1 câu, không so sánh/không ví dụ ứng dụng
  → theo ràng buộc (a), chỉ được chọn remember/understand → nguồn có giải
  thích NGẮN "vì sao" (restricts access ĐỂ protect data integrity) → chọn
  understand (không phải remember thuần túy vì có yếu tố quan hệ nhân-quả).
  Kết quả: bloom_level=understand, verb="giải thích", objective_text="Sau
  khi hoàn thành module, người học có thể giải thích cách Encapsulation gom
  nhóm dữ liệu và phương thức, đồng thời kiểm soát quyền truy cập để bảo vệ
  tính toàn vẹn dữ liệu."
  sub_objectives=["Mô tả cách dữ liệu và phương thức được gộp vào 1 class",
  "Giải thích vì sao kiểm soát quyền truy cập giúp bảo vệ tính toàn vẹn dữ
  liệu"]

Ví dụ 2 — nguồn có so sánh/phân tích thật (không chỉ định nghĩa) — LƯU Ý: ví
dụ minh họa dưới đây KHÔNG lấy từ bất kỳ tài liệu thật nào trong hệ thống,
chỉ để minh họa CÁCH suy luận khi nguồn có so sánh:
  Khái niệm: "TCP vs UDP". Nguồn có: định nghĩa TCP (hướng kết nối, đảm bảo
  thứ tự gói tin), định nghĩa UDP (không kết nối, không đảm bảo thứ tự), VÀ
  1 đoạn so sánh riêng "TCP guarantees delivery via acknowledgment and
  retransmission... UDP has lower latency, suited for real-time streaming;
  TCP suited for file transfer where reliability matters more than speed".
  Lập luận: nguồn KHÔNG chỉ định nghĩa riêng lẻ — có hẳn phần so sánh cơ chế
  + trường hợp sử dụng giữa 2 giao thức → theo ràng buộc (b), PHẢI chọn cấp
  độ cao hơn understand, không được mặc định hạ xuống → đây là yêu cầu chỉ
  ra mối quan hệ/khác biệt giữa 2 thành phần → chọn analyze.
  Kết quả: bloom_level=analyze, verb="phân tích", objective_text="Sau khi
  hoàn thành module, người học có thể phân tích sự khác biệt về cơ chế
  truyền tải, độ tin cậy và trường hợp sử dụng giữa TCP và UDP."
  sub_objectives=["So sánh cơ chế đảm bảo tin cậy (acknowledgment/
  retransmission) giữa TCP và UDP", "Phân biệt đặc tính độ trễ và thứ tự gói
  tin của mỗi giao thức", "Xác định trường hợp sử dụng phù hợp cho từng giao
  thức (truyền file vs streaming thời gian thực)"]
"""


class LearningObjective(BaseModel):
    bloom_level: BloomLevel
    verb: str = Field(description="Động từ Bloom's dùng trong objective_text")
    objective_text: str
    sub_objectives: list[str] = Field(
        min_length=2, max_length=3,
        description="2-3 Enabling Objectives (sub-LO), mỗi cái nhắm 1 khía cạnh thành phần khác nhau "
        "của objective_text -- dùng để ép Content bao phủ đủ rộng nguồn, không chỉ 1 góc hẹp",
    )


# Tầng 3 (GD2 — LLM-as-Judge / Self-Correction): LO trước đây sinh 1 lần
# duy nhất, không ai kiểm lại -- cùng lớp lỗ hổng đã vá cho Quiz
# (bloom_level_pass), giờ áp dụng cho chính LO. 1 vòng retry best-effort,
# giống hệt pattern MAX_CORRECTION_RETRY của quiz_graph.py.
MAX_LO_CORRECTION_RETRY = 1

LO_VERIFY_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là bộ kiểm tra chất lượng Learning Objective (Critic), dựa trên các đoạn
nguồn được cung cấp.

Chấm đúng 3 tiêu chí:
- verb_pass: objective_text có bắt đầu bằng 1 động từ hành động ĐO LƯỜNG ĐƯỢC
  không -- đặc biệt: có dùng các từ CẤM "hiểu"/"biết"/"học"/"nắm được"/"nắm
  vững"/"có kiến thức về"/"làm quen với" làm động từ hành động không (nếu có
  -> fail, kể cả khi cấp độ Bloom's ghi đúng)?
- atomic_pass: objective_text có chỉ chứa ĐÚNG 1 mục tiêu hiệu năng duy nhất
  không, hay bị gộp 2+ hành động khác nhau vào 1 câu (vd nối bằng "và"/"cũng
  như" 2 động từ khác cấp độ nhận thức)?
- grounded_pass: objective_text VÀ sub_objectives có bám đúng nội dung nguồn
  được cung cấp không, không suy diễn/thêm khẳng định ngoài nguồn?

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để
dùng làm feedback sinh lại).
"""


class LOVerification(BaseModel):
    verb_pass: bool
    verb_reason: str
    atomic_pass: bool
    atomic_reason: str
    grounded_pass: bool
    grounded_reason: str


def _verify_learning_objective(lo: LearningObjective, context_chunks: list[Chunk]) -> LOVerification:
    prompt = (
        f"LEARNING OBJECTIVE ĐÃ SINH:\n"
        f"bloom_level: {lo.bloom_level}\n"
        f"verb: {lo.verb}\n"
        f"objective_text: {lo.objective_text}\n"
        f"sub_objectives: {lo.sub_objectives}\n\n"
        f"{format_source_chunks(context_chunks)}\n\n"
        f"Kiểm tra verb_pass, atomic_pass, grounded_pass của Learning Objective trên."
    )
    return complete_structured(
        prompt, LOVerification, system_instruction=LO_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
    )


def generate_learning_objective(
    concept: Concept, context_chunks: list[Chunk]
) -> tuple[LearningObjective, LOVerification]:
    feedback: str | None = None
    lo: LearningObjective | None = None
    check: LOVerification | None = None
    for _ in range(MAX_LO_CORRECTION_RETRY + 1):
        prompt = (
            f"KHÁI NIỆM CỐT LÕI: {concept.name}\n"
            f"TÓM TẮT: {concept.summary}\n\n"
            f"{format_source_chunks(context_chunks)}\n\n"
            f"Sinh 1 Learning Objective cho khái niệm trên, dựa trên các đoạn nguồn đã cung cấp."
        )
        if feedback:
            prompt += (
                f"\n\nLƯU Ý: lần sinh trước KHÔNG ĐẠT vì lý do sau -- sửa đúng lỗi này, "
                f"không lặp lại: {feedback}"
            )
        # temperature=0.0: phan loai bloom_level la tac vu co can cu (dua tren
        # do sau nguon), khong phai sang tac -- giam sampling randomness de
        # cung 1 concept+context luon ra cung 1 ket luan.
        lo = complete_structured(
            prompt, LearningObjective, system_instruction=LO_SYSTEM_INSTRUCTION, temperature=0.0
        )

        check = _verify_learning_objective(lo, context_chunks)
        if check.verb_pass and check.atomic_pass and check.grounded_pass:
            return lo, check

        reasons = []
        if not check.verb_pass:
            reasons.append(f"Verb fail: {check.verb_reason}")
        if not check.atomic_pass:
            reasons.append(f"Atomic fail: {check.atomic_reason}")
        if not check.grounded_pass:
            reasons.append(f"Grounded fail: {check.grounded_reason}")
        feedback = "; ".join(reasons)

    return lo, check  # best-effort sau khi hết retry budget, giống pattern quiz_graph.py
