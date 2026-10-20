from __future__ import annotations

import re
from urllib.parse import urlparse


def humanize_url(url: str) -> str:
    """Describe a URL without making a listener hear protocol and path noise."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = parsed.path.strip("/")
        port = parsed.port
    except ValueError:
        return "link"
    if not host:
        return "link"

    github = (
        re.fullmatch(
            r"([^/]+)/([^/]+?)(?:\.git)?(?:/(pull|issues|commit|releases/tag)/([^/]+))?/?",
            path,
        )
        if host == "github.com"
        else None
    )
    if github:
        owner, repository, kind, identifier = github.groups()
        if kind == "pull":
            return f"pull request number {identifier} on {repository}"
        if kind == "issues":
            return f"issue number {identifier} on {repository}"
        if kind == "commit":
            return f"commit {identifier[:8]} on {repository}"
        if kind == "releases/tag":
            return f"release {identifier} of {repository}"
        return f"the {repository} repository on GitHub"

    gitlab = re.fullmatch(r"(.+?)/-/(merge_requests|issues)/(\d+)/?", path) if host == "gitlab.com" else None
    if gitlab:
        repository = gitlab.group(1).split("/")[-1]
        noun = "merge request" if gitlab.group(2) == "merge_requests" else "issue"
        return f"{noun} number {gitlab.group(3)} on {repository}"

    if host in {"localhost", "127.0.0.1", "::1"}:
        return f"local service on port {port}" if port else "local service"
    if host == "npmjs.com" or host.endswith(".npmjs.com"):
        package = path.removeprefix("package/")
        return f"the {package} package on npm" if package else "npm"
    if host == "t.me":
        destination = path.split("/", 1)[0]
        return f"{destination} on Telegram" if destination else "Telegram"
    if host == "tailscale.com" or host.endswith(".tailscale.com"):
        return "Tailscale"
    if host == "youtu.be" or host.endswith(".youtube.com"):
        return "YouTube"
    return host


def _speak_markdown_link(match: re.Match[str], *, humanize_links: bool) -> str:
    label, url = match.group(1).strip(), match.group(2).strip()
    if not humanize_links:
        return label
    destination = humanize_url(url)
    return f"{label}, link to {destination}" if label else f"link to {destination}"


def _speak_bare_url(match: re.Match[str], *, humanize_links: bool) -> str:
    url = match.group(0)
    punctuation = ""
    while url.endswith((".", ",", "!", "?", ":", ";")):
        punctuation = url[-1] + punctuation
        url = url[:-1]
    if not humanize_links:
        return url + punctuation
    return f"link to {humanize_url(url)}{punctuation}"


def markdown_to_speech_text(markdown: str, *, speak_code: bool = False, humanize_links: bool = True) -> str:
    """Render Markdown into stable listening text without changing its source."""
    if not markdown.strip():
        return ""
    output: list[str] = []
    in_code = False
    code_language = ""
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        fence = re.match(r"^\s*```\s*([^`]*)$", line)
        if fence:
            if not in_code:
                in_code = True
                code_language = fence.group(1).strip()
                if speak_code:
                    output.append("Code block" + (f", language {code_language}." if code_language else "."))
                else:
                    output.append("Code block omitted from audio.")
            else:
                if speak_code:
                    output.append("End code block.")
                in_code = False
                code_language = ""
            continue
        if in_code:
            if speak_code and line:
                output.append(line)
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+[.)]\s+", "", line)
        line = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"Image: \1", line)
        line = re.sub(
            r"\[([^]]*)\]\((https?://[^)]+)\)",
            lambda match: _speak_markdown_link(match, humanize_links=humanize_links),
            line,
        )
        line = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(
            r"(?<!\()https?://[^\s<>)]+",
            lambda match: _speak_bare_url(match, humanize_links=humanize_links),
            line,
        )
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"[*_~]", "", line)
        line = re.sub(r"\s*\|\s*", ", ", line)
        if line.strip() and not re.fullmatch(r"[-:, ]+", line):
            output.append(line.strip())
        elif output and output[-1] != "":
            output.append("")
    rendered = "\n".join(output).strip()
    if not rendered:
        return markdown
    return rendered
