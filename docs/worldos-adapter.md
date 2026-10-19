# WorldOS adapter contract

Target: `claudes-world/worldos`, `origin/dev` at **17eeee1**
(`17eeee17d619841848853769fc95adaeb548f6bc`). This is an integration contract;
this repository contains no host patch. Paths below are relative to
`apps/control-plane/src/worldos_control_plane_app/` at that revision.

**Boundary.** Add an utterance-level preparation step in the control plane's
render path **after the host claims the utterance, before per-chunk
`tts_renditions` are created**. Never compile or synthesize at enqueue. The current
`tts.py:TtsRenditionWorker.process_one()` claims an already-created rendition,
then calls `provider.render(claim)`; calling the compiler separately inside that
per-chunk call would select lessons independently for every chunk. The utterance boundary
is a host change still required at this revision, not an existing package hook.

**Modules to wire.** `speech.py` currently normalizes Markdown and splits source
speech. Move lesson-aware utterance compilation ahead of that splitting and retain
the original source for presentation. `store_modules/runtime_state.py` currently
creates voice chunks and `tts_renditions`, and materializes companion transcript
presentations with `build_presentations(...)` before publishing. Defer/fence their
publishable state until preparation finishes. `tts.py:TtsRenditionWorker` retains
host claims, fencing, provider spend policy and artifact custody; adapt it to
consume the prepared utterance result without synthesizing it again.
`speech_transcript.py:build_presentations()` currently accepts source/title only;
its `render()` silently skips unknown section kinds, including `lesson`.
Extend that builder and renderer to accept the **executed** lesson manifest and
emit the numbered, collapsible **Vietnamese lesson** block. Package `Section`
objects are not wire-compatible with the host's version-1 presentation mappings.
Preserve the host's title, link and message-partition rules when mapping them.
`file_ipc.py` uses that builder and `render_optional` for companion publication;
both it and `store_modules/runtime_state.py` must consume the revised presentation.

**Inputs and result.** Pass original Markdown; a `Profile` resolved from the lane's
English voice, explicit Vietnamese roster/weights, pacing and model settings;
`learner_id` (default `liam`); shared local state/cache paths; optional injected
`ModelCall`; and host-wrapped provider objects. Credentials stay in those objects.
Use one monotonic deadline bounded by the utterance claim, including time to
persist the result. `model="none"` still teaches the reserved closing phrase.
There is no `spend_hook` parameter. The provider contract is
`synthesize(clip, *, timeout) -> bytes`; it must honor timeout and make one attempt.
A minimal host wrapper, with host-defined budget/accounting callbacks, is:

```python
class ProviderWrapper:
    def __init__(self, inner, authorize, record_attempt):
        self.inner, self.authorize, self.record = inner, authorize, record_attempt

    def synthesize(self, clip, *, timeout):
        token = self.authorize(clip.provider, clip.voice, len(clip.text))
        status = "failed_or_uncertain"
        try:
            audio = self.inner.synthesize(clip, timeout=timeout)
            status = "succeeded"
            return audio
        finally:
            self.record(token, status)  # reconcile uncertainty using host policy
```

```python
plan = better_speech.compile(text, profile, model_call=model_call, deadline=deadline)
rendering = better_speech.render(plan, providers, clip_cache, deadline=deadline)
```

`rendering` returns `audio_path` (Ogg/Opus or `None`), `artifact_identity` (rendition
custody key), `audio_sha256` (file checksum), **executed** `plan`, structured
`sections`, Markdown and `diagnostics`. Text-only results have neither audio key.
Copy the temporary audio into host artifact custody, then clean up its temporary
directory. Cache hits call no provider/wrapper. Optional lesson failure produces
complete English; total synthesis failure retains a text result. Keep the host's
existing English/text delivery path available, with no new refusal or required
field for ordinary notes.

**Custody, transcript, send.** Key rendition custody on `Rendering.artifact_identity`
and persist the executed plan with it. This key is the digest of
`(Plan.identity, Plan.executed_digest)`. `executed_digest` hashes the ordered
executed clips and lessons; it is set only when `executed=True` and is stable
across processes. `Plan.identity` still hashes
`(learner_id, curriculum_snapshot_digest, source_text, profile_digest)` for input
grouping and voice seeding. **Do not reuse a rendition by `Plan.identity` alone**:
changed ledger state, model placement or English fallback can change its execution.
Identical clips can still share the clip cache. Persist
`rendering.plan.to_dict()` (executed lesson manifest), input identity, artifact
identity, file checksum and durable audio reference **before publishing**.
Use that stored executed plan on retry, rather than recompiling a previously
prepared artifact. On English fallback, remove the lesson section from the
companion transcript message that `store_modules/runtime_state.py` materializes
before publishing; updating only `file_ipc.py`'s final rendering is insufficient.
Publish the audio and its revised companion from the same persisted outcome.

After **confirmed send**, call
`record_delivered(rendering.plan, delivery_id, delivered_at)` with the host's stable
logical delivery ID and timezone-aware confirmation timestamp. Retry a failed
ledger write with that same ID/time and stored executed plan; replay is idempotent.
Never resend after an observed successful send. Preparation and publishing attempts
count nothing. Echoes count once per phrase; late acknowledgements replay in
chronological order. This local SQLite ledger is separate from host delivery
custody; its diagnostic failure must not undo a successful delivery.
