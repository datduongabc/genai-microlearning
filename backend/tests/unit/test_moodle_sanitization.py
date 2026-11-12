from app.export.lesson import clean_html as _clean_html


def test_clean_html_strips_event_handler_attributes():
    cleaned = _clean_html('<p onclick="alert(1)">click me</p>')
    assert "onclick" not in cleaned
    assert "click me" in cleaned


def test_clean_html_keeps_allowed_formatting():
    cleaned = _clean_html("<h1>Title</h1><ul><li><strong>bold</strong> item</li></ul>")
    assert "<h1>Title</h1>" in cleaned
    assert "<strong>bold</strong>" in cleaned
