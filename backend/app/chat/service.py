"""FAQ / hỏi-đáp tương tác — chatbot RAG theo course, grounded qua retrieve()
(pipeline duy nhất, dùng chung với Content/Slides/Quiz)."""

from pydantic import BaseModel, Field

from app.llm.gateway import complete, complete_structured
from app.models import Chunk

HISTORY_TURNS = 4

CONDENSE_SYSTEM_INSTRUCTION = """Bạn viết lại câu hỏi mới nhất của người dùng thành 1 câu hỏi ĐỘC LẬP, đầy đủ
ngữ cảnh, dùng để tìm kiếm tài liệu — thay thế đại từ ("nó", "cái đó", "vấn
đề này"...) bằng danh từ cụ thể lấy từ lịch sử hội thoại. Nếu câu hỏi mới đã
độc lập sẵn (không cần ngữ cảnh), giữ nguyên. CHỈ trả về câu hỏi đã viết lại,
không thêm giải thích/tiền tố nào khác.
"""


def condense_query(message: str, history: list[dict]) -> str:
    """Query rewriting cho hội thoại nhiều lượt: retrieve() không thấy lịch sử
    hội thoại, nên 1 câu hỏi dùng đại từ tham chiếu lượt trước ("nó có mấy
    tính chất?") sẽ tìm sai/không ra chunk liên quan nếu dùng nguyên văn.
    Viết lại thành câu hỏi độc lập trước khi truy xuất (retrieve() vẫn nhận
    câu hỏi gốc để trả lời tự nhiên, chỉ dùng bản viết lại để tìm kiếm)."""
    if not history:
        return message
    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    prompt = f"LỊCH SỬ HỘI THOẠI:\n{history_text}\n\nCÂU HỎI MỚI NHẤT: {message}"
    rewritten = complete(prompt, system_instruction=CONDENSE_SYSTEM_INSTRUCTION).strip()
    return rewritten or message

CHAT_SYSTEM_INSTRUCTION = """Bạn là trợ lý hỏi-đáp cho môn học. Chỉ được trả lời dựa trên các đoạn nguồn
(source chunk) được cung cấp bên dưới — KHÔNG được bịa đặt/suy diễn thông tin
ngoài nguồn, kể cả khi bạn biết câu trả lời đúng từ nơi khác.

Nếu câu hỏi KHÔNG được trả lời trong các đoạn nguồn, PHẢI trả lời rõ "Tài liệu
môn học không đề cập nội dung này" — không được cố trả lời bằng kiến thức
ngoài nguồn.

Khi trích dẫn (citations), CHỈ được dùng đúng chunk_id đã cho trong SOURCE
CHUNK — không được tự bịa chunk_id.

Ngôn ngữ trả lời khớp ngôn ngữ chính của câu hỏi/nguồn (tiếng Việt có dấu đầy
đủ nếu là tiếng Việt).
"""


class ChatCitation(BaseModel):
    chunk_id: str
    source_ref: str = ""


class ChatAnswer(BaseModel):
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list)


def answer_question(query: str, context_chunks: list[Chunk], history: list[dict]) -> ChatAnswer:
    if not context_chunks:
        return ChatAnswer(answer="Tài liệu môn học không đề cập nội dung này.", citations=[])

    history_text = ""
    if history:
        history_text = "\n\nLỊCH SỬ HỘI THOẠI TRƯỚC (gần nhất trước):\n" + "\n".join(
            f"{m['role']}: {m['content']}" for m in history
        )

    excerpts = "\n\n---\n\n".join(
        f"chunk_id={c.id} source_ref={c.source_ref}\n{c.content}" for c in context_chunks
    )

    prompt = f"SOURCE CHUNKS:\n{excerpts}{history_text}\n\nCÂU HỎI: {query}"

    result = complete_structured(prompt, ChatAnswer, system_instruction=CHAT_SYSTEM_INSTRUCTION)

    # Không tin citation model tự echo ra — chỉ giữ citation có chunk_id THỰC
    # SỰ nằm trong context_chunks đã truy xuất (tránh model bịa chunk_id nhìn
    # hợp lý nhưng không tồn tại/không phải chunk đã dùng).
    valid_ids = {str(c.id) for c in context_chunks}
    result.citations = [c for c in result.citations if c.chunk_id in valid_ids]
    return result
