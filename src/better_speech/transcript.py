"""Speech presentation only; source Markdown and synthesis inputs stay immutable.

Ported from world-os-bridge-live/marketplace/plugins/comms/voice.ts:
deriveTranscriptTitle, extractLinks, chunkEscapedForTelegram and
richTranscriptMarkdown. Python code points replace JS Array.from. Optional
lesson sections come only from completed execution, never requested plans.
"""

from __future__ import annotations

import html
import re

from .curriculum import canonical_phrase, grouped_literal

_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://(?:[^()<>\s]|\([^()<>\s]*\))+)\)")
_BARE = re.compile(r"\bhttps?://(?:[^\s<>()\[\]]|\([^\s<>()\[\]]*\))+")


def extract_links(text: str) -> list[dict[str, str]]:
    """Copy of v1 extractLinks: named destinations first, deduped by URL."""
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    spans = []
    for match in _LINK.finditer(text):
        label, url = match.groups()
        if url not in seen:
            links.append({"label": label, "url": url})
            seen.add(url)
        spans.append(match.span())
    for match in _BARE.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        url = match.group().rstrip(".,!?;:")
        if url not in seen:
            links.append({"label": url, "url": url})
            seen.add(url)
    return links


def derive_title(text: str) -> str:
    """v1 deriveTranscriptTitle, including its 60-code-point display clamp."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return "Voice note"
    sentence = re.match(r"^.*?[.!?](?=\s|$)", collapsed)
    value = (sentence.group() if sentence else collapsed).removesuffix(".")
    return value if len(value) <= 60 else value[:60].rstrip() + "…"


def _escape(text: str) -> str:
    # Preserve source Markdown, but never let source HTML close our details.
    return html.escape(text, quote=False)


def _label(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", _escape(text))


def _grouped_phrase(phrase: str, units: object) -> str:
    """Insert unit separators without replacing authored punctuation/text."""
    if not isinstance(units, list) or not units or any(not isinstance(unit, str) for unit in units):
        return phrase
    tokens = list(re.finditer(r"\w+", phrase))
    words = [canonical_phrase(t.group()) for t in tokens]
    groups = [re.findall(r"\w+", canonical_phrase(unit)) for unit in units]
    if any(not group for group in groups) or [word for group in groups for word in group] != words:
        return phrase
    result, cursor, count = [], 0, 0
    for group in groups[:-1]:
        count += len(group)
        cut = tokens[count].start()
        piece = phrase[cursor:cut]
        result.extend((piece, "• " if piece and piece[-1].isspace() else " • "))
        cursor = cut
    result.append(phrase[cursor:])
    return "".join(result)


def present(plan):
    """Only a completed plan supplies lesson authority; original Markdown stays intact."""
    from .types import Section

    sections = [Section("title", derive_title(plan.source))]
    if plan.executed and plan.lessons:
        lines = []
        for number, lesson in enumerate(plan.lessons, 1):
            row = lesson.record
            lines.append(f"{number}. **{_label(_grouped_phrase(row['phrase'], row.get('units')))}**")
            try:
                aligned = grouped_literal(row)
            except Exception:
                aligned = None
            literal = " • ".join(aligned) if aligned else row.get("literal", "")
            if isinstance(literal, list):
                literal = " • ".join(literal)
            if literal:
                lines.append("   *" + _label(literal) + "*")
            if row.get("hint"):
                lines.append("   *say: " + _label(row["hint"]) + "*")
            lines.append("   " + _label(row["translation"]))
        sections.append(Section("lesson", "\n".join(lines), True))
    sections.append(Section("transcript", plan.source, True))
    links = extract_links(plan.source)
    if links:
        sections.append(Section("links", "\n".join(f"- [{_label(v['label'])}](<{v['url']}>)" for v in links)))
    rendered = []
    for section in sections:
        if section.kind == "title":
            rendered.append("# " + _label(section.text))
        elif section.kind == "lesson":
            rendered.append(
                "<details>\n<summary>🇻🇳 Vietnamese lexicon</summary>\n\n" + section.text + "\n\n</details>"
            )
        elif section.kind == "transcript":
            rendered.append(
                "<details>\n<summary>Transcript</summary>\n\n" + _escape(section.text) + "\n\n</details>"
            )
        else:
            rendered.append("Links\n\n" + section.text)
    return tuple(sections), "\n\n".join(rendered) + "\n"
