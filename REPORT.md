# WOS2-1194 round4

Version: 0.2.5.

All 88 shipped pronunciation hints use attached ↗/↘/↓/⌄ tone marks, with bare
level syllables. Both former low labels map to ↓; the reading key keeps
final-stop clipping as a note. Piece separators are ` - `; multi-syllable words
follow the existing interlinear Vietnamese word grouping and use single spaces.
The English respellings and all other curriculum fields are preserved.

Each numbered lesson now presents bold Vietnamese, literal gloss, translation,
a blank line, and an indented fenced `say:` block. Custom hint newlines become
spaces so the say line stays inside its numbered item. Code content stays literal; backtick runs select a longer fence as needed.
README and metadata describe the new reading key.

The optional host regression imports the installed WorldOS speech_transcript.py
only when its path exists. It exercises `_with_lessons` and `render` with three
lessons, then parses the actual rich output. The host's `  \n` join leaves all
three fences intact in one ordered list with three list items. The two trailing
spaces become harmless whitespace in code content. Shipped hints need no host changes.

Validation:

- Mechanical comparison against the base: all 88 hint respellings and tones
  preserved; no other curriculum fields changed.
- Focused hint/transcript tests pass, including actual host rendering.
- `uv run ruff check .` and `git diff --check`: clean.
- One `codex exec --sandbox read-only -m gpt-6.1-sol` review completed.
  Its multiline-hint finding was fixed by normalizing hint newlines and covered
  in package and actual host regressions. Its HTML-entity finding was fixed for
  package Markdown by keeping code content literal; the host limitation below
  remains outside this repository.
- `uv run pytest -q`: 875 passed after review fixes. The fresh environment needed
  `uv sync --all-extras` for the existing optional-provider tests.

Constraints: the installed WorldOS host always HTML-escapes lesson text before
joining lines. For custom hints containing `<`, `>` or `&`, that turns those
characters into visible entities inside fenced code. Package Markdown preserves
them correctly, and none of the 88 shipped hints contains these characters.
Preserving these characters in host-rendered code requires a host change to
escape prose while leaving fenced code content literal. No WorldOS files were
changed by this package PR.
