"""Secrets and contact details taken out of a document's text as it is read, before anything stores it or sends
it on: the database, the embedding provider, the reranker, the chat model and, through citations, the learners.
A lecture handout can carry a lab's API key, a demo password or a student's phone number; none of them is what
a module or an answer should repeat. The uploaded file itself is untouched (it expires from the store).

Patterns are kept narrow on purpose: lecture text is full of numbers, code and formulas, and a pattern that
also matched "x = 10" or a year would damage the material it protects."""

import re

EMAIL = "[đã che email]"
PHONE = "[đã che số điện thoại]"
KEY = "[đã che khoá]"
PASSWORD = "[đã che mật khẩu]"

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
# Prefixes the providers give their keys and tokens, and a JSON Web Token's three base64url parts.
_KEYS = re.compile(
    r"\b(?:sk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
# "password = 'hunter22'", "mật khẩu: Abc@123", "API_KEY=...": the value only, and only when it looks like
# a credential (quoted, or a token with a digit or symbol), so "Password: a secret string" stays prose.
_ASSIGNED = re.compile(
    r"(?i)(?<![a-z])(pass(?:word|wd)?|pwd|mật khẩu|secret|api[_ -]?key|access[_ -]?token|auth[_ -]?token|token)"
    r"(\s*[:=]\s*)(?!\[đã che)"
    r"""(?:(["'])([^"'\n]{4,})\3|((?=\S*[\d@#$%^&*!_/+=-])\S{6,}))"""
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# Vietnamese mobiles and landlines (0 or +84 first) and other numbers written in the international form.
# Not a bare run of digits: in a lecture that is data far more often than a phone.
_PHONE = re.compile(r"(?<![\w.,])(?:\+\d{1,3}|00\d{2}|0)[\d .-]{7,15}\d(?!\d|[.,]\d)")
_SEPARATORS = re.compile(r"[ .-]")


def _phone(match: re.Match) -> str:
    number = match.group()
    groups = [g for g in _SEPARATORS.split(number) if g]
    digits = "".join(groups).lstrip("+")
    # Written in groups of two or more ("0912 345 678"); digits one by one ("0 5 1 2 3 ...") are data.
    if any(len(g) < 2 for g in groups[1:]) or len(number) - len(number.replace(" ", "")) > 4:
        return number
    if number.startswith("+") and not number.startswith("+84"):
        return PHONE if 8 <= len(digits) <= 15 else number
    if number.startswith("+84"):
        national = digits[2:]
    elif digits.startswith("0084"):
        national = digits[4:]
    else:
        national = digits[1:]
    mobile = len(national) == 9 and national[0] in "35789"
    landline = len(national) == 10 and national[0] == "2"
    return PHONE if mobile or landline else number


def _assigned(match: re.Match) -> str:
    return f"{match.group(1)}{match.group(2)}{PASSWORD}"


def redact(text: str) -> tuple[str, int]:
    """`text` with secrets and contact details replaced by a label, and how many were replaced."""
    count = 0

    def counted(replacement):
        def apply(match: re.Match) -> str:
            nonlocal count
            result = replacement(match) if callable(replacement) else replacement
            count += result != match.group()
            return result
        return apply

    text = _PRIVATE_KEY.sub(counted(KEY), text)
    text = _KEYS.sub(counted(KEY), text)
    text = _ASSIGNED.sub(counted(_assigned), text)
    text = _EMAIL.sub(counted(EMAIL), text)
    text = _PHONE.sub(counted(_phone), text)
    return text, count
