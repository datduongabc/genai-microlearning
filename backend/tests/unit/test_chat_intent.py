"""Greetings and thanks get a fixed reply without a search or a model call; questions never do."""

import pytest

from app.chat.intent import small_talk


@pytest.mark.parametrize(("message", "start"), [
    ("Xin chào", "Chào bạn!"),
    ("chào bạn nhé!!", "Chào bạn!"),
    ("Hello", "Hello!"),
    ("hi there 👋", "Hello!"),
    ("Cảm ơn nhiều ạ", "Không có gì!"),
    ("thanks a lot", "You're welcome!"),
    ("Tạm biệt", "Chào bạn, hẹn gặp lại!"),
    ("ok", "Bạn cứ hỏi tiếp"),
])
def test_small_talk_gets_a_fixed_reply_in_its_language(message, start):
    assert small_talk(message).startswith(start)


@pytest.mark.parametrize("message", [
    "Xin chào, heap là gì?",
    "Hi, what is a heap?",
    "chào hàng là gì trong marketing",
    "Cảm ơn, vậy còn round robin thì sao?",
    "OK vậy deadlock xảy ra khi nào",
    "Heap",
    "",
])
def test_a_question_is_never_small_talk(message):
    assert small_talk(message) is None
