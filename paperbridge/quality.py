"""Mechanical integrity checks, deliberately not an accuracy score."""
from pathlib import Path
from html import unescape
from urllib.parse import unquote, urlsplit
import re


def image_references(markdown):
    refs = re.findall(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)", markdown)
    refs += re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', markdown, flags=re.I)
    return list(dict.fromkeys(unescape(x) for x in refs))


def check_markdown(markdown, root):
    issues = []
    for ref in image_references(markdown):
        parsed = urlsplit(ref)
        if parsed.scheme or parsed.netloc:
            issues.append({"code": "nonlocal_image", "detail": ref})
            continue
        path = (Path(root) / unquote(parsed.path)).resolve()
        if not path.is_relative_to(Path(root).resolve()):
            issues.append({"code": "image_outside_bundle", "detail": ref})
        elif not path.is_file() or not path.stat().st_size:
            issues.append({"code": "missing_image", "detail": ref})
    if markdown.count("$$") % 2:
        issues.append({"code": "unbalanced_display_math", "detail": "$$"})
    if "\ufffd" in markdown:
        issues.append({"code": "replacement_characters", "detail": str(markdown.count("\ufffd"))})
    if not markdown.strip():
        issues.append({"code": "empty_markdown", "detail": ""})
    return issues


def flatten_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(flatten_text(v) for v in value)
    if isinstance(value, dict):
        return flatten_text(value.get("content", ""))
    return ""
