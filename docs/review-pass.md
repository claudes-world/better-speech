# Cold review of the week-one implementation

Reviewed against the stage-one brief and the approved stage-two refinements in
the conversation. `stage2-directive.md` was not found in the checkout or the
accessible source locations searched; comparison with a separate file of that
name remains unverified. No commit or push was made.

## Concrete findings fixed

Line numbers refer to the resulting tree, where each fix now lives.

| Finding | Location of fix | Evidence |
| --- | --- | --- |
| Invalid optional profile values could raise during serialization before the fail-open handler; malformed voice-pool JSON could also escape. Preserve a usable English narrator, disable the lesson, and diagnose invalid configuration. | `src/better_speech/config.py:14`, `src/better_speech/config.py:95` | `tests/test_review_regressions.py:29`, `:39` render complete English, rather than just asserting a mocked call. |
| Non-JSON curriculum data could raise again while hashing the failure result. Ledger-read failure was treated as an empty learning history and could reintroduce previously mastered phrases. Guard the snapshot digest and fail to English when the history is unavailable. | `src/better_speech/compiler.py:119`, `src/better_speech/compiler.py:132` | `tests/test_review_regressions.py:48`, `:56`. |
| A valid contextual selection could push the combined plan over the text cap and discard the reserved closing lesson. Build the reserved plan first and retain it if optional additions cannot form a valid plan. | `src/better_speech/compiler.py:202`, `src/better_speech/compiler.py:264` | `tests/test_review_regressions.py:66`; existing empty-response/boost reservation checks remain in `tests/test_compiler.py:18`. |
| Render setup, invalid optional manifests/configuration, artifact hashing and cleanup had paths outside the English fallback. Guard those phases; present the manifest before purchasing, use a separate English output path, and return text if English also fails. | `src/better_speech/rendering.py:34`, `src/better_speech/rendering.py:68`, `src/better_speech/rendering.py:119` | `tests/test_review_regressions.py:98`, `:126` assert audio/transcript outcomes and removal of fallback lessons. |
| Provider initialization and individual late returns, postprocessing, and repeated SQLite work could exceed their allocated time. Recompute remaining provider time after setup, reject late returns, bound hashing, reset SQLite busy timeout per statement, and check replay/commit against the shared deadline. Roll back incomplete event/projection writes. | `src/better_speech/providers/google.py:18`, `src/better_speech/providers/elevenlabs.py:19`, `src/better_speech/rendering.py:90`, `src/better_speech/rendering.py:112`, `src/better_speech/ledger.py:52`, `src/better_speech/ledger.py:70` | `tests/test_review_regressions.py:143`, `:192`, `:207`, `:229`; existing ffmpeg deadline tests retained. |
| Unused historical executor, segment, selection, pacing, rest-bearing rewriter, exposure administration, legacy cache-key and voice-role code was shipped alongside the actual runtime. Keep historical contracts/assets only under `tests/reference`; production retains the used ffmpeg program, SRS reduction and strict selection parser. Remove dead transport grouping/preflight and planner rest/role paths. | `src/better_speech/executor.py:1`, `src/better_speech/rewriter.py:1`, `src/better_speech/planner.py:29`, `src/better_speech/exposure.py:18`, `src/better_speech/cache.py:91`; moves listed below | Copied pure fixture tests remain runnable, and `docs/provenance.md` now distinguishes historical evidence from current behavior. |
| The host document implied lesson sections could simply be passed to the current host renderer. At `17eeee1`, `speech_transcript.py:153` accepts source/title only, and its renderer at `:202` ignores unknown kinds. Document the necessary builder/renderer changes and preserve host presentation rules. | `docs/worldos-adapter.md:16`, `:24`, `:78` | Read the exact Git objects at `17eeee17d619841848853769fc95adaeb548f6bc`; verified `TtsRenditionWorker` and the named modules. No obsolete worker is named. |
| One cache test claimed to cover provider failure without exercising it; adapter argv tests alone did not establish the audible trust boundary. Rename/strengthen the hit test, add single-attempt provider-failure behavior, and drive all three adapters through real subprocesses using fake CLI executables. | `tests/test_cache_io.py:56`, `:97`, `tests/test_review_regressions.py:159` | Assert returned audio, purchase counts, complete source coverage and absence of model-authored text in clips; valid and extra-text responses covered for each adapter. |

## Checked and retained

- The strict parser accepts only offered phrase/slot IDs; no model-authored prose
  or legacy `rest` field can enter clips. English lesson glosses come from the
  trusted curriculum. The model-free lesson path still produces a real Opus file.
- The registry selects exactly one adapter. Existing tests register a fourth
  adapter and make every other adapter fail if invoked; a selected-adapter error
  preserves deterministic placement, without a fallback chain.
- Real multiprocessing tests cover simultaneous cache misses purchasing once,
  four ledger writers preserving all 40 unique delivery events, and two learner
  queue writers preserving both additions. Cache-unavailable and read-only paths
  still render bilingual audio. A provider exception is propagated once rather
  than mistaken for cache I/O and purchased again.
- Chronological replay gives identical schedule, first/last-heard values and
  counts in both acknowledgement orders. A duplicate delivery keeps its original
  timestamp. Preparation does not record exposure.
- `Plan.identity`, timezone-based boost expiry, executed-manifest custody and
  post-confirmation acknowledgement remain as specified. English fallback removes
  the companion lesson before publication in the documented host contract.
- Git tracks only the text `LICENSE`. There are no committed binary artifacts to
  remove. `.artifacts/` remains ignored; the existing paid model-free listening
  sample stays local. Environments, caches and distribution archives are ignored.

## Remaining work and scope

No known week-one implementation defect remains from this pass. Independent
comparison with the missing directive file and a live authenticated AGY smoke
test remain unverified. Fake executable tests verify its adapter behavior but do
not establish subscription/model availability on a user's account. The actual
WorldOS patch remains a separate host session, as requested.

Provider/model callbacks supplied by a host must honor their timeout. The library
checks deadlines before and after calls, but cannot forcibly interrupt arbitrary
synchronous Python callbacks or blocked filesystem operations. Process isolation
for those callbacks, a new async API, cache eviction/admin commands, extra
providers, document roles, heteronyms, Kokoro and an executable host patch are
declined as gold-plating or explicitly deferred scope.

## Validation

- `pytest -q`: **700 passed** (30 new regression cases in this pass).
- Ruff lint and formatting checks pass.
- Distribution build and a clean core-only wheel installation are checked; the
  historical reference modules/assets are excluded from the runtime wheel.

## Files changed in this pass

The following inventory compares this pass with its starting snapshot. Since the
implementation was uncommitted throughout, Git still shows these as new relative
to the repository's initial `LICENSE` commit. See [the complete tree inventory](files-changed.md)
for every proposed file.

### Modified

- `README.md`
- `docs/files-changed.md`
- `docs/provenance.md`
- `docs/worldos-adapter.md`
- `src/better_speech/assembly.py`
- `src/better_speech/cache.py`
- `src/better_speech/compiler.py`
- `src/better_speech/config.py`
- `src/better_speech/curriculum.py`
- `src/better_speech/executor.py`
- `src/better_speech/exposure.py`
- `src/better_speech/ledger.py`
- `src/better_speech/planner.py`
- `src/better_speech/providers/elevenlabs.py`
- `src/better_speech/providers/google.py`
- `src/better_speech/rendering.py`
- `src/better_speech/rewriter.py`
- `src/better_speech/transcript.py`
- `src/better_speech/voices.py`
- `tests/reference/identity.py`
- `tests/reference/planner.py`
- `tests/reference/polyglot.py`
- `tests/test_cache_io.py`
- `tests/test_cli_learning.py`
- `tests/test_polyglot_curriculum.py`
- `tests/test_polyglot_exposure.py`
- `tests/test_polyglot_pacing.py`
- `tests/test_polyglot_rewriter.py`
- `tests/test_polyglot_segments.py`
- `tests/test_polyglot_selection.py`
- `tests/test_providers.py`
- `tests/test_speech_cache.py`
- `tests/test_speech_executor.py`
- `tests/test_speech_executor_audio.py`

### Added (including reference moves)

- `docs/review-pass.md`
- `tests/reference/curriculum.py`
- `tests/reference/data/polyglot/curriculum-pr1010-provenance.json`
- `tests/reference/data/polyglot/curriculum-pr1010.json`
- `tests/reference/data/polyglot/curriculum-v1.json`
- `tests/reference/executor.py`
- `tests/reference/exposure.py`
- `tests/reference/legacy_pacing.py`
- `tests/reference/rewriter.py`
- `tests/reference/segments.py`
- `tests/reference/selection.py`
- `tests/test_review_regressions.py`

### Removed from the runtime tree (retained as test references)

- `src/better_speech/data/polyglot/curriculum-pr1010-provenance.json`
- `src/better_speech/data/polyglot/curriculum-pr1010.json`
- `src/better_speech/data/polyglot/curriculum-v1.json`
- `src/better_speech/legacy_pacing.py`
- `src/better_speech/segments.py`
- `src/better_speech/selection.py`
