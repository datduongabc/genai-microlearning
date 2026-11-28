"""Messages that are not questions about the course: a greeting, thanks, a goodbye. They get a fixed reply at
once, without a search or a model call: the search would find passages about something else, and the model
would be asked to answer from them.

Only a message that is nothing but such words counts: "Xin chào, heap là gì?" is a question."""

import re

# Words that may surround a greeting without making it a question ("chào bạn nhé", "thanks a lot").
_FILLER = r"(?:\s+(?:bạn|ạ|nhé|nha|nhá|nhen|ad|admin|bot|thầy|cô|anh|chị|em|mình|you|a lot|so much|very much|there|all|everyone|again))*"
_KINDS = {
    "greeting": r"(?:xin\s+chào|chào|chao|hello|hi|hey|alo|good\s+(?:morning|afternoon|evening))",
    "thanks": r"(?:cảm\s+ơn|cám\s+ơn|cảm\s+ơn\s+nhiều|thanks?|thank\s+you|thx|tks)",
    "bye": r"(?:tạm\s+biệt|bye|goodbye|see\s+you|hẹn\s+gặp\s+lại)",
    "ack": r"(?:ok|oke|okay|ừ|ừm|vâng|dạ|được\s+rồi|got\s+it)",
}
_PATTERNS = {kind: re.compile(rf"^{words}{_FILLER}$", re.IGNORECASE) for kind, words in _KINDS.items()}
_EDGES = re.compile(r"^[\W_]+|[\W_]+$")
_ENGLISH = re.compile(r"\b(?:hello|hi|hey|good|thanks?|thank|thx|tks|bye|goodbye|see|okay|got|you)\b", re.IGNORECASE)
_VIETNAMESE = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]|\b(?:chao|alo|oke)\b",
                         re.IGNORECASE)

_REPLIES = {
    "vi": {
        "greeting": "Chào bạn! Mình trả lời câu hỏi dựa trên tài liệu của môn học. Bạn muốn hỏi gì?",
        "thanks": "Không có gì! Bạn cứ hỏi tiếp nếu cần.",
        "bye": "Chào bạn, hẹn gặp lại!",
        "ack": "Bạn cứ hỏi tiếp về nội dung môn học nhé.",
    },
    "en": {
        "greeting": "Hello! I answer questions from the course documents. What would you like to ask?",
        "thanks": "You're welcome! Ask again any time.",
        "bye": "Goodbye, see you!",
        "ack": "Feel free to ask another question about the course.",
    },
}


def small_talk(message: str) -> str | None:
    """The fixed reply to a message that is only a greeting, thanks, goodbye or acknowledgement, else None."""
    text = re.sub(r"\s+", " ", _EDGES.sub("", message.strip()))
    if not text or len(text) > 40:
        return None
    for kind, pattern in _PATTERNS.items():
        if pattern.match(text):
            language = "en" if _ENGLISH.search(text) and not _VIETNAMESE.search(text) else "vi"
            return _REPLIES[language][kind]
    return None
