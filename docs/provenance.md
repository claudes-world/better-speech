# Copied sources and week-one changes

The package starts with copies of existing pure Python rather than a new
implementation of the lesson engine. Reference paths are read-only source
locations on the implementation host; they are not runtime dependencies.

Source root: `worldos-wt/handoff-voice-e1/apps/control-plane/src/worldos_control_plane_app/`.

| Source | Package destination | Changes |
| --- | --- | --- |
| `polyglot_curriculum.py` | `curriculum.py`, `tests/reference/curriculum.py` | Runtime retains validation, literal alignment and normalization; retired asset loaders/concept grouping live only with historical fixtures. Added current-sample overlay and safe learner paths. |
| `polyglot_segments.py` | `tests/reference/segments.py` | Retained pure historical validation outside the runtime. |
| `polyglot_selection.py` | `tests/reference/selection.py` | Historical selection is fixture-only; current boost/due reservation is in `compiler.py`. |
| `polyglot_rewriter.py` | `rewriter.py`, `tests/reference/rewriter.py` | Runtime accepts strict ID/slot selections only; retired rest-bearing responses are fixture-only. |
| `polyglot_planner.py` | `planner.py` | Removed legacy provider/voice resolvers; explicit voices, deterministic closing placement, pending/landed echo; complete source fallback. |
| `polyglot_voices.py` | `voices.py`, `tests/reference/voices.py` | Runtime uses an explicit weighted pool; retired roles, baked-in rosters/IDs and provider defaults are fixture-only. |
| `speech_pacing.py` | `pacing.py` | Retained punctuation compilation and precedence. |
| `polyglot_exposure.py` | `exposure.py`, `tests/reference/exposure.py` | Runtime retains pure 1/3/7/21-day reduction; unused historical import, administration and selection helpers are fixture-only. No persistence calls. |
| `polyglot_pacing.py` | `tests/reference/legacy_pacing.py` | Retained pure old pause-mark validation outside the runtime. |
| `speech_boundaries.py` | `boundaries.py` | Retained trusted paragraph/fence boundary calculation. |
| `speech_transcript.py` | `transcript.py` | Retained title/link helpers; numbered collapsible lesson from executed manifest; removed unused transport grouping. |
| `speech_executor.py` | `executor.py`, `tests/reference/executor.py` | Runtime retains only the ffmpeg program; retired async custody execution is fixture-only. Public orchestration is in `rendering.py`. |
| `speech_assembly.py` | `assembly.py` | Retained ffmpeg concatenate/silence/loudnorm with deadline and scratch cleanup. |
| `speech_cache.py` | `cache.py`, `tests/reference/identity.py` | Runtime retains canonical clip identity and adds checksummed disk entries, file locks and atomic writes; legacy phrase keys are fixture-only. |
| `speech_model_calls.py` | `tests/reference/model_calls.py` | Retained pure response parsing for copied stage tests; replaced historical routing with the new `ModelCall` protocol and registry in production. |
| `speech_helper.py` bounded runner | `models/process.py` | Copied subprocess pattern with limits, process-group termination and API-key-free environment. |
| `tts.py` ElevenLabs synthesis | `providers/elevenlabs.py` | Copied request fields; explicit credentials, timeout and client injection. |

`speech_enrichment.py` and `speech_polyglot.py` are retained as pure test references
(`tests/reference/stages.py`, `polyglot.py`). The old `polyglot_config.py` is a
host configuration-release CLI, so the standalone TOML loader in `config.py`
replaces it. `speech_execution_plan.py` composes host preparation contracts;
public frozen shapes and composition use `types.py` and `compiler.py` instead.
`polyglot_ledger.py`, `store_modules`, psycopg and database migrations were never
copied into the package. `ledger.py` is new local SQLite persistence; `learning.py`
is the new atomic learner queue. The current runtime does not invoke the historical
coffee/timezone selection rule retained for fixture parity in `tests/reference/selection.py`.

Markdown normalization is copied from `speech.py` on WorldOS `origin/dev` at
`17eeee1`, with no host imports. Google plain-text request construction follows
`/home/claude/bin/md-speak`'s `google_synthesize`; no AI descriptions or document
roles were copied. `compile`, provider routing, registry, CLI and current result
contracts are week-one integration code around the copied pure components.

`data/curriculum.vi.json` is the current **54-phrase**
`world-os/marketplace/plugins/comms/polyglot-curriculum.json`, copied byte-for-byte.
That source has no lesson classes, 47 rows without units, and 54 aligned literal
arrays. `curriculum.vi.metadata.json` is an explicit classification/unit metadata
overlay. Unannotated learner phrases get one whole-phrase unit, with a one-element
tiered list where required by the copied validator. Historical curriculum assets
under `tests/reference/data/polyglot/` are retained outside the shipped package so their pure fixture tests retain
the original expectations. `examples/voices.vi.json` is the founder's explicit
11-voice locked pool, copied verbatim from
`~/.config/worldos/polyglot-voice-weights.json`; it is never loaded implicitly.

The copied pure tests are under `tests/test_polyglot_*.py` and
`tests/test_speech_*.py`. `tests/reference/` preserves pure historical planner,
voice and stage contracts needed by those tests, with package-local imports.
These historical tests do not claim that retired providers or defaults are
available through the public package. `fixtures/enrichment/` contains the 19
existing enrichment golden JSON fixtures (segments, sequences, echo, pauses,
pacing, settings, selection, voices, SRS, curriculum, cache keys and executor).
Their source evidence labels and hashes are preserved; filesystem evidence paths
inside the manifest are provenance only and are not opened by the tests.

The founder's parity audit
`PARITY-voice-units10-11-20260920.md` supplies the pause/echo/SRS rulings. Week-one
amendments deliberately replace stale defaults: all Vietnamese uses configured
ElevenLabs voices, English is Google plain text, no hard-coded Vietnamese pool,
learning reservation precedes model selection, and the numbered lesson is
collapsible with the exact header **Vietnamese lesson**. Model failures preserve
deterministic lessons. No HTTP model adapter is included.
