"""
Format assistant output for WhatsApp Cloud API text messages.

WhatsApp does not render Markdown. Meta's handler sends `data.message` as the body;
Twilio uses `whatsapp.text` when present. We normalize both by formatting at the source.

Similar goals to Twilio path in user-backend (clean text, links on their own line).
"""

from __future__ import annotations

import re

# [label](https://...)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
# **bold** or __bold__
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
# `code`
_MD_CODE_RE = re.compile(r"`([^`]+)`")
# ### headings at line start
_MD_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
# *italic* or _italic_ (single delimiters, not list bullets)
_MD_ITALIC_STAR_RE = re.compile(
    r"(?<![*\n])\*([^*\n]+)\*(?!\*)",
)
_MD_ITALIC_UNDER_RE = re.compile(
    r"(?<!_)_([^_\n]+)_(?!_)",
)
# Ordered list "1. " / "12) " at line start (after strip)
_ORDERED_PREFIX_RE = re.compile(r"^\s*(?:\d+)[.)]\s+")


def _strip_markdown_line(line: str) -> str:
    """Remove bold/code/italic from a single line (repeat for nested patterns)."""
    s = line
    for _ in range(4):
        prev = s
        s = _MD_BOLD_RE.sub(lambda m: (m.group(1) or m.group(2) or "").strip(), s)
        s = _MD_CODE_RE.sub(lambda m: m.group(1), s)
        s = _MD_ITALIC_STAR_RE.sub(r"\1", s)
        s = _MD_ITALIC_UNDER_RE.sub(r"\1", s)
        s = s.replace("**", "").replace("__", "")
        if s == prev:
            break
    return s


def format_assistant_text_for_whatsapp(text: str) -> str:
    """Strip Markdown and normalize lists for WhatsApp plain-text messages."""
    if not text:
        return ""

    out = text.replace("\r\n", "\n")

    # Links: put URL on its own line (readable on mobile)
    out = _MD_LINK_RE.sub(lambda m: f"{m.group(1).strip()}\n{m.group(2)}", out)

    # Headings -> plain line
    out = _MD_HEADING_RE.sub("", out)

    # Line-by-line: numbered lists, bullets, nested **bold** in list items
    lines: list[str] = []
    for raw in out.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            lines.append("")
            continue

        had_ordered = bool(_ORDERED_PREFIX_RE.match(line.lstrip()))
        after_ordered = _ORDERED_PREFIX_RE.sub("", line.lstrip()) if had_ordered else line

        s = after_ordered.lstrip()
        if s.startswith("- ") or (s.startswith("* ") and not s.startswith("* *")):
            rest = s[2:].lstrip()
            line = "• " + _strip_markdown_line(rest)
        elif had_ordered:
            line = "• " + _strip_markdown_line(after_ordered.strip())
        else:
            line = _strip_markdown_line(line)

        lines.append(line)

    out = "\n".join(lines)

    # Collapse excessive blank lines
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out
