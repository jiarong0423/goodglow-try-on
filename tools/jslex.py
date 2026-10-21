"""Minimal JS lexical scanner: find the outermost strings ('…' "…") and template strings (`…`), correctly handling strings, templates and comments nested inside ${…}.
The original single-regex split went wrong on a template string nested inside a template string (observed in stage 2,, in two places in app.js).
Returns only outermost tokens; nested ones are part of the enclosing template. Returns [(start, end, kind)], kind being one of str | tpl | comment | regex.
"""
from __future__ import annotations

REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")


def _skip_str(s, i):
    q, n, j = s[i], len(s), i + 1
    while j < n and s[j] != q:
        j += 2 if s[j] == "\\" else 1
    return min(j + 1, n)


def _skip_regex(s, i):
    n, j, cls = len(s), i + 1, False
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            cls = True
        elif c == "]":
            cls = False
        elif c == "/" and not cls:
            j += 1
            while j < n and s[j].isalpha():
                j += 1
            return j
        elif c == "\n":
            return i + 1
        j += 1
    return n


def _skip_template(s, i):
    n, j = len(s), i + 1
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
        elif c == "`":
            return j + 1
        elif c == "$" and j + 1 < n and s[j + 1] == "{":
            j = _skip_expr(s, j + 2)
        else:
            j += 1
    return n


def _prev_sig(s, i):
    j = i - 1
    while j >= 0 and s[j] in " \t\r\n":
        j -= 1
    return s[j] if j >= 0 else ""


def _skip_expr(s, i):
    n, depth, j = len(s), 1, i
    while j < n and depth:
        c = s[j]
        if c in "'\"":
            j = _skip_str(s, j)
        elif c == "`":
            j = _skip_template(s, j)
        elif c == "/" and s[j:j + 2] == "//":
            k = s.find("\n", j)
            j = n if k < 0 else k
        elif c == "/" and s[j:j + 2] == "/*":
            k = s.find("*/", j + 2)
            j = n if k < 0 else k + 2
        elif c == "/" and (_prev_sig(s, j) in REGEX_PREV or _prev_sig(s, j) == ""):
            j = _skip_regex(s, j)
        else:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            j += 1
    return j


def tokens(src: str):
    out, n, i = [], len(src), 0
    while i < n:
        c = src[i]
        if c == "/" and src[i:i + 2] == "//":
            k = src.find("\n", i)
            k = n if k < 0 else k
            out.append((i, k, "comment"))
            i = k
        elif c == "/" and src[i:i + 2] == "/*":
            k = src.find("*/", i + 2)
            k = n if k < 0 else k + 2
            out.append((i, k, "comment"))
            i = k
        elif c in "'\"":
            k = _skip_str(src, i)
            out.append((i, k, "str"))
            i = k
        elif c == "`":
            k = _skip_template(src, i)
            out.append((i, k, "tpl"))
            i = k
        elif c == "/" and (_prev_sig(src, i) in REGEX_PREV or _prev_sig(src, i) == ""):
            k = _skip_regex(src, i)
            out.append((i, k, "regex"))
            i = k
        else:
            i += 1
    return out
