"""Learning objective, written in one call from the retrieved passages."""

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
nguyên tắc "1 Main LO + 2-4 Sub-objectives" đã kiểm chứng qua thực tiễn
microlearning của Moodle/Raccoon Gang và Konstantly/Beedeez): objective_text
là Terminal Objective (mục tiêu tổng thể) — module vẫn CHỈ CÓ 1 module, KHÔNG
tách thành nhiều module. Ngoài ra PHẢI viết thêm 2-4 sub_objectives (đủ để
bao phủ khái niệm trong một bài học 5-10 phút, không chia nhỏ cho đủ số), mỗi cái
là 1 Enabling Objective cụ thể (dạng "[động từ] ...", KHÔNG cần lặp lại "Sau
khi hoàn thành module..."), mỗi sub-objective nhắm đúng 1 khía cạnh/năng lực
THÀNH PHẦN khác nhau cần đạt để hoàn thành Terminal Objective — không được 2
sub-objective trùng lặp cùng 1 khía cạnh. Cấp độ Bloom's của từng
sub-objective PHẢI ≤ cấp độ của objective_text (không được cao hơn mục tiêu
tổng thể nó phục vụ). Mục đích: ép Content bám đủ rộng theo từng khía cạnh
thay vì chỉ tổng hợp 1 góc hẹp của nguồn.

Ví dụ mẫu (Few-Shot CoT — 2 ví dụ đầy đủ chuỗi lập luận, KHÔNG copy nguyên
văn cho chủ đề mới, chỉ học đúng CÁCH suy luận; cả hai ví dụ cố ý thuộc một
lĩnh vực không liên quan tới tài liệu đang xử lý):

Ví dụ 1 — nguồn chỉ định nghĩa/liệt kê (không giải thích sâu):
  Khái niệm: "Quang hợp". Nguồn: "Quang hợp là quá trình thực vật dùng năng
  lượng ánh sáng để tổng hợp glucose từ CO2 và nước, đồng thời giải phóng oxy."
  Lập luận: nguồn CHỈ định nghĩa 1 câu, không so sánh/không ví dụ ứng dụng
  → theo ràng buộc (a), chỉ được chọn remember/understand → nguồn có nêu quan
  hệ đầu vào - đầu ra (ánh sáng, CO2, nước TẠO RA glucose và oxy) → chọn
  understand (không phải remember thuần túy vì có yếu tố quan hệ nhân-quả).
  Kết quả: bloom_level=understand, verb="giải thích", objective_text="Sau
  khi hoàn thành module, người học có thể giải thích cách quang hợp dùng năng
  lượng ánh sáng để tạo ra glucose từ CO2 và nước."
  sub_objectives=["Nêu các chất tham gia và sản phẩm của quang hợp",
  "Giải thích vai trò của năng lượng ánh sáng trong quá trình tạo glucose"]

Ví dụ 2 — nguồn có so sánh/phân tích thật (không chỉ định nghĩa):
  Khái niệm: "Nguyên phân và giảm phân". Nguồn có: định nghĩa nguyên phân
  (1 lần phân chia, tạo 2 tế bào con giống hệt tế bào mẹ), định nghĩa giảm
  phân (2 lần phân chia liên tiếp, tạo 4 tế bào con có số nhiễm sắc thể giảm
  một nửa), VÀ 1 đoạn so sánh riêng về vai trò: nguyên phân phục vụ sinh
  trưởng và thay thế tế bào, giảm phân tạo giao tử cho sinh sản hữu tính.
  Lập luận: nguồn KHÔNG chỉ định nghĩa riêng lẻ — có hẳn phần so sánh cơ chế
  + vai trò giữa 2 quá trình → theo ràng buộc (b), PHẢI chọn cấp độ cao hơn
  understand, không được mặc định hạ xuống → đây là yêu cầu chỉ ra mối quan
  hệ/khác biệt giữa 2 thành phần → chọn analyze.
  Kết quả: bloom_level=analyze, verb="phân tích", objective_text="Sau khi
  hoàn thành module, người học có thể phân tích sự khác biệt về cơ chế phân
  chia, kết quả và vai trò giữa nguyên phân và giảm phân."
  sub_objectives=["So sánh số lần phân chia và số tế bào con của hai quá
  trình", "Phân biệt số nhiễm sắc thể trong tế bào con của mỗi quá trình",
  "Xác định vai trò của từng quá trình (sinh trưởng vs sinh sản hữu tính)"]
"""


class LearningObjective(BaseModel):
    bloom_level: BloomLevel
    verb: str = Field(description="Động từ Bloom's dùng trong objective_text")
    objective_text: str
    sub_objectives: list[str] = Field(
        min_length=2, max_length=4,
        description="2-4 Enabling Objectives (sub-LO), mỗi cái nhắm 1 khía cạnh thành phần khác nhau "
        "của objective_text -- dùng để ép Content bao phủ đủ rộng nguồn, không chỉ 1 góc hẹp",
    )


def generate_learning_objective(concept: Concept, context_chunks: list[Chunk]) -> LearningObjective:
    """One call, no self-check round: the schema holds the shape (2-4 sub-objectives, a Bloom level
    from the table) and the Content and quiz checks downstream read the objective against the source."""
    prompt = (
        f"KHÁI NIỆM CỐT LÕI: {concept.name}\n"
        f"TÓM TẮT: {concept.summary}\n\n"
        f"{format_source_chunks(context_chunks)}\n\n"
        f"Sinh 1 Learning Objective cho khái niệm trên, dựa trên các đoạn nguồn đã cung cấp."
    )
    # temperature=0.0: phan loai bloom_level la tac vu co can cu (dua tren
    # do sau nguon), khong phai sang tac -- giam sampling randomness de
    # cung 1 concept+context luon ra cung 1 ket luan.
    return complete_structured(prompt, LearningObjective, system_instruction=LO_SYSTEM_INSTRUCTION, temperature=0.0)
