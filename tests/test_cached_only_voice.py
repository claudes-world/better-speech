"""Cached-only voices speak only verified cached clips; a miss substitutes a pool voice."""

import json
import time
import unicodedata
from dataclasses import replace

from better_speech import Profile, Voice, compile, render
from better_speech.cache import ClipCache

from .test_model_free_audio import EmptyLedger, ToneProvider, tone

POOL = (
    Voice("vanca", region="central", weight=4, cached_only=True),
    Voice("native-central", region="central", weight=2),
    Voice("native-south", region="southern", weight=2),
)


def profile(voices=POOL):
    return Profile(model="none", voices=voices)


def plan_with(voice_id, voices=POOL):
    for seed in range(200):
        plan = compile("# Status\n\nThe update is ready.", profile(voices), ledger=EmptyLedger(), seed=seed)
        if any(c.voice == voice_id for c in plan.clips):
            return plan
    raise AssertionError("no seed selected the cached-only voice")


def providers():
    provider = ToneProvider()
    return provider, {"google": provider, "elevenlabs": provider}


def test_compiler_attaches_substitute_from_non_cached_pool_voice():
    plan = plan_with("vanca")
    cached = [c for c in plan.clips if c.voice == "vanca"]
    assert cached and all(c.cached_only and c.purpose == "lesson" for c in cached)
    for clip in cached:
        substitute = json.loads(clip.substitute_json)
        assert substitute["voice"] in ("native-central", "native-south")
        assert not substitute["cached_only"] and substitute["rate"] in (0.9, 0.75)
        assert substitute["echo"] == clip.echo and substitute["rate"] == clip.rate
    assert not any(c.cached_only for c in plan.clips if c.voice != "vanca")


def test_cache_hit_speaks_cached_voice_without_synthesis(tmp_path):
    plan = plan_with("vanca")
    cache = ClipCache(tmp_path / "cache")
    for clip in plan.clips:
        if clip.cached_only:
            cache.get_or_synthesize(
                replace(clip, text=unicodedata.normalize("NFC", clip.text)),
                lambda: tone(660), deadline=time.monotonic() + 30, diagnostics=[],
            )
    provider, table = providers()
    result = render(plan, table, cache)
    assert result.audio_path and result.plan.status == "bilingual", result.diagnostics
    assert not any(c.voice == "vanca" for c in provider.calls)
    assert any(c.voice == "vanca" for c in result.plan.clips)
    assert ("voice", "cached_only_substituted") not in {(d.stage, d.reason) for d in result.diagnostics}


def test_cache_miss_substitutes_pool_voice_and_never_synthesizes(tmp_path):
    plan = plan_with("vanca")
    provider, table = providers()
    result = render(plan, table, tmp_path / "cache")
    assert result.audio_path and result.plan.status == "bilingual", result.diagnostics
    assert not any(c.voice == "vanca" for c in provider.calls)
    assert not any(c.voice == "vanca" for c in result.plan.clips)
    assert ("voice", "cached_only_substituted") in {(d.stage, d.reason) for d in result.diagnostics}
    # The substitute fills the same lesson slot with the same pause.
    assert len(result.plan.clips) == len(plan.clips)
    for planned, spoken in zip(plan.clips, result.plan.clips):
        if planned.cached_only:
            assert spoken.voice != "vanca" and spoken.pause_after_s == planned.pause_after_s
            assert spoken.phrase_key == planned.phrase_key and spoken.echo == planned.echo


def test_cached_only_without_any_substitute_degrades_to_english(tmp_path):
    only = (Voice("vanca", region="central", weight=4, cached_only=True),)
    plan = plan_with("vanca", only)
    assert all(c.substitute_json == "" for c in plan.clips if c.cached_only)
    provider, table = providers()
    result = render(plan, table, tmp_path / "cache")
    assert result.audio_path and result.plan.status == "english"
    assert not any(c.voice == "vanca" for c in provider.calls)


def test_default_voice_flag_leaves_profile_digest_unchanged():
    data = profile(POOL[1:]).to_dict()
    assert all("cached_only" not in v for v in (data["english"], *data["voices"]))
    assert profile(POOL).to_dict()["voices"][0]["cached_only"] is True
    assert Profile.from_dict(profile(POOL).to_dict()).voices[0].cached_only is True


def test_voices_file_cached_only_list(tmp_path):
    from better_speech.config import load_profile

    (tmp_path / "pool.json").write_text(json.dumps({
        "weights": {"vanca": 4, "native": 2}, "cached_only": ["vanca"], "regions": {"vanca": "central"},
    }))
    (tmp_path / "profile.toml").write_text('model = "none"\nvoices_file = "pool.json"\n')
    voices = {v.voice: v for v in load_profile(tmp_path / "profile.toml").voices}
    assert voices["vanca"].cached_only and not voices["native"].cached_only


def test_cached_clip_evicted_after_check_still_plays(tmp_path, monkeypatch):
    plan = plan_with("vanca")
    cache = ClipCache(tmp_path / "cache")
    for clip in plan.clips:
        if clip.cached_only:
            cache.get_or_synthesize(
                replace(clip, text=unicodedata.normalize("NFC", clip.text)),
                lambda: tone(660), deadline=time.monotonic() + 30, diagnostics=[],
            )
    lookup = ClipCache.lookup
    calls = []

    def once(self, clip):  # Second read of any key behaves as evicted.
        key = self.path(clip)
        if key in calls:
            return None
        calls.append(key)
        return lookup(self, clip)

    monkeypatch.setattr(ClipCache, "lookup", once)
    provider, table = providers()
    result = render(plan, table, cache)
    assert result.plan.status == "bilingual", result.diagnostics
    assert any(c.voice == "vanca" for c in result.plan.clips)
    assert not any(c.voice == "vanca" for c in provider.calls)


def test_default_plan_dict_omits_new_clip_fields():
    plan = compile("Note.", profile(POOL[1:]), ledger=EmptyLedger())
    rows = plan.to_dict()["clips"] + plan.to_dict()["fallback_clips"]
    assert rows and all("cached_only" not in r and "substitute_json" not in r for r in rows)
    cached = plan_with("vanca").to_dict()["clips"]
    assert any(r.get("cached_only") for r in cached)
