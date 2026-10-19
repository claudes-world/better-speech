# better-speech

Short Vietnamese lessons inside agents' English voice notes. Python 3.12+,
AGPL-3.0. Standalone: no WorldOS dependency, database server, or model HTTP API.

Install ffmpeg on your host, then run this ten-line quickstart from the checkout:

```sh
uv sync --all-extras
export ELEVENLABS_API_KEY='your-key'
export GOOGLE_APPLICATION_CREDENTIALS='/path/to/google-service-account.json'
printf 'The update is ready.\n' > /tmp/note.md
uv run speak learn 'Cảm ơn nha!' --gloss 'Thanks!' --profile examples/profile.toml
uv run speak plan /tmp/note.md --profile examples/profile.toml
uv run speak render /tmp/note.md --out /tmp/note.ogg --profile examples/profile.toml
uv run speak due --profile examples/profile.toml
uv run speak learned cam-on-nha --profile examples/profile.toml
uv run pytest
```

Use the `id` returned by `learn` for `learned` (the sample phrase above is
`cam-on-nha`). The example selects `model = "none"`: a closing lesson and its
learning echo work without any model executable. Its Vietnamese roster and lead
weights are explicit in `examples/voices.vi.json`; the library has no default
Vietnamese roster. Google receives plain text for English; ElevenLabs receives
Vietnamese. Hosts must configure and authorize both providers.

```python
from better_speech import compile, render, record_delivered
from better_speech.config import load_profile
from better_speech.providers.google import GoogleProvider
from better_speech.providers.elevenlabs import ElevenLabsProvider

profile = load_profile("examples/profile.toml")
plan = compile("The update is ready.", profile)
rendering = render(
    plan,
    {
        "google": GoogleProvider(),  # Google application credentials
        "elevenlabs": ElevenLabsProvider(api_key),  # supplied by your host
    },
    cache="/var/cache/my-agent/speech",
)
# Persist rendering.plan, artifact_identity, audio and sections before publishing.
# After confirmed send only:
receipt = record_delivered(rendering.plan, delivery_id, delivered_at)
```

`compile(text, profile, *, model_call=None, ledger=None, deadline=None)` preserves
the source Markdown and produces a serializable `Plan` of `Clip`s: text,
language, provider, model, voice, rate, format and pause-after. `Plan.to_dict()` /
`Plan.from_dict()` round-trip its frozen profile, curriculum digest, lesson
manifest and diagnostics. The highest eligible learner phrase is reserved for
the closing lesson: boosted first, then due, then one unseen introduction.
Intensity 1/2/3 limits placement counts to 2/4/4, with at most two new phrases,
three contextual connectors and one closing lesson. Enriched text over the
inherited 12,000 UTF-16-unit budget becomes complete English; source is never
truncated. Numbered lessons come only from the executed plan.

`render(plan, providers, cache=None, *, deadline=None)` takes a mapping of provider
names to objects implementing `synthesize(clip, *, timeout) -> bytes`. The host
wraps these objects to enforce its budget and record every paid attempt. Cache
hits never call a provider. The result contains a caller-owned temporary Ogg/Opus
path, SHA-256 artifact identity, executed plan, diagnostic records, Markdown and
structured title/lesson/transcript/links sections. The **Vietnamese lesson** block
is collapsible. Move/copy the returned file to durable storage and remove its
temporary directory after use. The CLI handles this when writing `--out`.

Optional lesson failures yield the complete English note and remove its lesson
section. A contextual model failure retains the reserved deterministic lesson.
If English synthesis or ffmpeg is also unavailable, the result has
`audio_path=None`, `status="text"`, and the full transcript. CLI degradation
prints diagnostics to stderr and keeps exit status zero; invalid commands or
unreadable input files return a command error. No send or exposure is inferred
from preparation.

Both phases accept an absolute `time.monotonic()` deadline; pass the **same**
deadline to bound the whole operation. Profile timeouts bound each phase and each
provider/model call; lesson work reserves up to `fallback_reserve_s` (at most half
the remaining time) for English. Provider implementations must honor `timeout`.
Subscription subprocesses, cache lock acquisition and ffmpeg are bounded;
artifact hashing and presentation also share the deadline. Arbitrary injected
callbacks must honor their timeout; the library checks again when they return.

Set one profile setting to choose the optional contextual placer:

| `model` | Adapter | Model setting/default |
| --- | --- | --- |
| `none` | deterministic closing lesson only | no executable |
| `claude` (library default) | `claude -p` subscription login | `claude_model = "claude-haiku-5-5"` |
| `codex` | `codex exec`, ChatGPT login | `codex_model = "gpt-6-luna"` |
| `agy` | Antigravity `agy --print`, subscription login | `agy_model = "gemini-3-flash"` |

There is no adapter fallback chain. The selected CLI may choose only offered
curriculum IDs and boundary IDs; it cannot supply audible text. Missing login,
unsupported CLI/model, malformed/late output or an empty response never removes
the reserved closing lesson. CLI model availability depends on the installed
subscription tools; all three adapters are tested through real subprocesses
using fake CLI executables, without a live authenticated AGY session. API-key environment variables are excluded
from subprocesses. Inject one `ModelCall` object (`__call__(payload, *, timeout)
-> str`) or add a module and `models.register_model(name, factory)` to extend the
registry without changing the compiler.

Learner additions are written atomically under
`$XDG_DATA_HOME/better-speech/learners/<learner-hash>/curriculum.json`; exposure
events live in the single `ledger.sqlite3` there. `state_dir` overrides the root.
`learn` boosts to next midnight in the profile's IANA `timezone` (default
`Asia/Ho_Chi_Minh`) and keeps the learning echo until `learned <key>` marks it
landed. Neither command records a hearing. `due` lists boosted/due phrases.
`--learner` overrides the profile's default `liam` for every command.

Call `record_delivered(rendering.plan, delivery_id, delivered_at)` only after a
confirmed send; use the same ID and timestamp on replay. Each phrase counts once
per delivery, including echoes. SQLite uses WAL, a busy timeout and
`BEGIN IMMEDIATE`; chronological event replay preserves the 1/3/7/21-day schedule
when acknowledgements arrive out of order. Early hearings increase the count
without advancing the interval. Failed acknowledgements return diagnostics for
the host to retry, never trigger a resend.

The content-addressed cache defaults to
`$XDG_CACHE_HOME/better-speech/clips`. Keys cover provider, model, voice, rate,
output format, NFC rendered text and effective voice settings. Per-key file locks
prevent duplicate purchases on concurrent misses while available; bounded lock
timeouts or unwritable storage bypass the cache with diagnostics. Writes use a
checksum envelope, temporary file and `os.replace`. Cache bypass can purchase an
identical clip more than once; host budget wrappers remain authoritative.

`Plan.identity` hashes `(learner_id, curriculum_snapshot_digest, source_text,
profile_digest)`. Key rendition custody on it so learners or curriculum changes
never share a lesson rendering; identical clips still share the clip cache.
Persist the **executed** manifest: a requested bilingual plan can render English.
See the exact [WorldOS integration contract](docs/worldos-adapter.md) and
[source provenance](docs/provenance.md).

Install with `pip install 'better-speech[polyglot,google,elevenlabs]'` or use uv.
`polyglot` is an intentionally empty convenience extra: curriculum and SQLite
use the standard library. Provider imports are lazy; planning and learning need
neither SDK. Tests use fake model/provider transports and real ffmpeg, with no
paid calls. Contributions require assignment to **claudes-world**; see
[CONTRIBUTING.md](CONTRIBUTING.md).
