"""A module as a SCORM 1.2 package, for an LMS the system is not connected to.

Publishing to Moodle needs a linked account and creates editable Moodle activities. A SCORM
package needs neither: the lecturer uploads one zip to any LMS that takes SCORM (Moodle, Canvas,
Blackboard...), and the LMS records the quiz score and whether the learner passed. SCORM 1.2 is
used rather than 2004 because every one of them runs it fully, Moodle included.

The package is one SCO: a single page with the reading parts, the practice questions answered one
at a time with feedback, and the quiz, scored when submitted. Its text comes from the same
sanitized builders as the Moodle Book (app/export/lesson.py). The answer key is in the page, as
in any SCORM quiz scored by its own page, so the score is a learning record, not an exam.
"""

import html
import io
import json
import re
import zipfile

from app.export.lesson import (
    build_book_chapters,
    build_questions,
    explanations,
    find_component,
    plain,
)
from app.models import ComponentType, Module

# A learner passes at 80% of the quiz: a short check of one objective should be mostly right.
# It is written into the manifest too, so the LMS applies the same threshold as the page.
MASTERY_SCORE = 80


def _script_json(value: object) -> str:
    """JSON that is safe inside a <script> element: no "</script>" or "<!--" can close it."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _manifest(identifier: str, title: str) -> str:
    title = html.escape(title, quote=True)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="genailms-{identifier}" version="1.0"
  xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
  xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">
  <metadata>
    <schema>ADL SCORM</schema>
    <schemaversion>1.2</schemaversion>
  </metadata>
  <organizations default="org-1">
    <organization identifier="org-1">
      <title>{title}</title>
      <item identifier="item-1" identifierref="res-1">
        <title>{title}</title>
        <adlcp:masteryscore>{MASTERY_SCORE}</adlcp:masteryscore>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="res-1" type="webcontent" adlcp:scormtype="sco" href="index.html">
      <file href="index.html"/>
    </resource>
  </resources>
</manifest>
"""


_STYLE = """
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.6;color:#1d2433;background:#f7f7f4;margin:0}
main{max-width:46rem;margin:0 auto;padding:2rem 1rem 4rem}
h1{font-size:1.75rem;line-height:1.25;margin:0 0 1.5rem}
h2{font-size:1.25rem;margin:2.5rem 0 .75rem;padding-top:1rem;border-top:1px solid #d9dad3}
h3{font-size:1.05rem;margin:1.5rem 0 .5rem}
section.chapter.sub h2{font-size:1.05rem;border-top:0;margin-top:1.25rem;padding-top:0}
pre,code{background:#eceee8;border-radius:4px}pre{padding:.75rem;overflow-x:auto}
fieldset{border:1px solid #d9dad3;border-radius:6px;background:#fff;margin:0 0 1rem;padding:1rem;min-width:0}
legend{float:left;width:100%;padding:0;margin:0 0 .5rem;font-weight:600}
legend+*{clear:both}
label.choice{display:flex;gap:.5rem;align-items:flex-start;margin:.35rem 0;cursor:pointer}
label.choice input{margin-top:.35rem}
.feedback{margin-top:.75rem;padding:.6rem .75rem;border-radius:4px;background:#eef3fb}
.feedback.right{background:#e7f4ea}.feedback.wrong{background:#fbecea}
button{font:inherit;font-weight:600;padding:.5rem 1rem;border-radius:6px;border:1px solid #1e4fa3;background:#1e4fa3;color:#fff;cursor:pointer}
button.secondary{background:#fff;color:#1e4fa3}
button:disabled{opacity:.55;cursor:default}
#quiz-result{font-weight:600;margin-top:1rem}
"""

_SCRIPT = """
(function () {
  var data = JSON.parse(document.getElementById('module-data').textContent);
  var mastery = data.mastery;

  // The LMS puts the SCORM 1.2 API object on a parent frame or on the window that opened this one.
  function findApi(win) {
    for (var i = 0; win && i < 10; i++) {
      if (win.API) return win.API;
      if (win.parent === win) break;
      win = win.parent;
    }
    return null;
  }
  var api = findApi(window) || (window.opener ? findApi(window.opener) : null);
  var finished = false;
  if (api) {
    api.LMSInitialize('');
    var status = api.LMSGetValue('cmi.core.lesson_status');
    if (status === 'not attempted' || status === '') api.LMSSetValue('cmi.core.lesson_status', 'incomplete');
    api.LMSCommit('');
  }
  function finish() {
    if (api && !finished) { finished = true; api.LMSFinish(''); }
  }
  window.addEventListener('pagehide', finish);
  window.addEventListener('beforeunload', finish);

  function chosen(fieldset) {
    var picked = fieldset.querySelector('input:checked');
    return picked ? Number(picked.value) : null;
  }
  function showFeedback(fieldset, question, choice) {
    var box = fieldset.querySelector('.feedback');
    var right = choice === question.correct;
    box.className = 'feedback ' + (right ? 'right' : 'wrong');
    box.textContent = right ? 'Đúng.' : 'Chưa đúng. Đáp án đúng là ' + String.fromCharCode(65 + question.correct) + '.';
    if (question.explanation) {
      var p = document.createElement('div');
      p.innerHTML = question.explanation; // already sanitized on the server
      box.appendChild(p);
    }
    box.hidden = false;
  }

  // Practice: one question at a time, feedback straight away, nothing recorded.
  data.practice.forEach(function (question, n) {
    var fieldset = document.getElementById('practice-' + n);
    fieldset.querySelector('button').addEventListener('click', function () {
      var choice = chosen(fieldset);
      if (choice === null) return;
      showFeedback(fieldset, question, choice);
    });
  });

  // Quiz: all questions, then one score, which is what the LMS records.
  var submit = document.getElementById('quiz-submit');
  if (submit) submit.addEventListener('click', function () {
    var right = 0;
    var unanswered = 0;
    data.quiz.forEach(function (question, n) {
      if (chosen(document.getElementById('quiz-' + n)) === null) unanswered++;
    });
    var result = document.getElementById('quiz-result');
    if (unanswered) { result.textContent = 'Còn ' + unanswered + ' câu chưa trả lời.'; return; }
    data.quiz.forEach(function (question, n) {
      var fieldset = document.getElementById('quiz-' + n);
      var choice = chosen(fieldset);
      if (choice === question.correct) right++;
      showFeedback(fieldset, question, choice);
      fieldset.querySelectorAll('input').forEach(function (input) { input.disabled = true; });
    });
    var score = Math.round(100 * right / data.quiz.length);
    var passed = score >= mastery;
    result.textContent = 'Điểm: ' + right + '/' + data.quiz.length + ' (' + score + '%). ' +
      (passed ? 'Đạt.' : 'Chưa đạt, cần ' + mastery + '%.');
    submit.disabled = true;
    if (api) {
      api.LMSSetValue('cmi.core.score.min', '0');
      api.LMSSetValue('cmi.core.score.max', '100');
      api.LMSSetValue('cmi.core.score.raw', String(score));
      api.LMSSetValue('cmi.core.lesson_status', passed ? 'passed' : 'failed');
      api.LMSCommit('');
    }
  });
})();
"""


def _demote_headings(content: str) -> str:
    """Headings inside a chapter's text sit under the chapter title (h2), not beside it. The text is
    sanitized without attributes, so its heading tags are exactly <h1> to <h3>."""
    return re.sub(r"<(/?)h([1-3])>", lambda m: f"<{m.group(1)}h{min(int(m.group(2)) + 2, 4)}>", content)


def _question_html(prefix: str, n: int, question: dict, button: str | None) -> str:
    choices = "".join(
        f'<label class="choice"><input type="radio" name="{prefix}-{n}" value="{i}">'
        f"<span><strong>{chr(65 + i)}.</strong> {choice}</span></label>"
        for i, choice in enumerate(question["choices"])
    )
    action = f'<button type="button" class="secondary">{button}</button>' if button else ""
    return (
        f'<fieldset id="{prefix}-{n}"><legend>Câu {n + 1}</legend>'
        f'<div class="stem">{question["stem"]}</div>{choices}{action}'
        f'<div class="feedback" hidden></div></fieldset>'
    )


def _page(module: Module, title: str) -> str:
    chapters = build_book_chapters(module)
    body = []
    for chapter in chapters:
        css = "chapter sub" if chapter["subchapter"] else "chapter"
        body.append(
            f'<section class="{css}"><h2>{html.escape(chapter["title"])}</h2>'
            f'<div class="chapter-body">{_demote_headings(chapter["content"])}</div></section>'
        )

    def questions(component_type: ComponentType) -> list[dict]:
        component = find_component(module, component_type)
        if component is None or not component.questions:
            return []
        return build_questions(component, explanations(module, component_type))

    practice = questions(ComponentType.practice)
    quiz = questions(ComponentType.quiz)
    if practice:
        body.append('<section><h2>Luyện tập</h2><p>Trả lời từng câu để xem giải thích. Phần này không tính điểm.</p>')
        body.extend(_question_html("practice", n, q, "Kiểm tra") for n, q in enumerate(practice))
        body.append("</section>")
    if quiz:
        body.append(f'<section><h2>Kiểm tra</h2><p>Trả lời tất cả rồi nộp bài. Đạt khi đúng từ {MASTERY_SCORE}%.</p>')
        body.extend(_question_html("quiz", n, q, None) for n, q in enumerate(quiz))
        body.append('<button type="button" id="quiz-submit">Nộp bài</button><p id="quiz-result" role="status"></p></section>')

    def key(items: list[dict]) -> list[dict]:
        return [{"correct": q["correct_index"], "explanation": q["explanation"]} for q in items]

    data = {"mastery": MASTERY_SCORE, "practice": key(practice), "quiz": key(quiz)}
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main><h1>{html.escape(title)}</h1>{''.join(body)}</main>"
        f'<script type="application/json" id="module-data">{_script_json(data)}</script>'
        f"<script>{_SCRIPT}</script></body></html>"
    )


def build_package(module: Module) -> bytes:
    """The zip an LMS imports: imsmanifest.xml at the root and the one page it points to."""
    title = plain(module.title) or "Mô-đun"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("imsmanifest.xml", _manifest(module.id.hex, title))
        archive.writestr("index.html", _page(module, title))
    return output.getvalue()
