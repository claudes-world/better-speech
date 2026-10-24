from datetime import datetime, timedelta, timezone

from better_speech import compile
from better_speech.curriculum import runtime_curriculum
from better_speech.exposure import anchor_floor
from better_speech.learning import due
from tests.test_model_free_audio import EmptyLedger, profile

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def test_anchor_floor_preserves_short_intervals_and_other_phrases():
    rows = runtime_curriculum(profile())
    anchors = [r for r in rows if r.get('anchor')]
    assert {r['id'] for r in anchors} == {'cam-on-nha', 'xin-loi', 'xin-loi-noi-lai'}
    records = {r['ledger_identity']: {'lastHeard': NOW.isoformat(),
                                    'dueAt': (NOW + timedelta(days=21)).isoformat()} for r in rows}
    adjusted = anchor_floor(rows, records, 7)
    for row in rows:
        expected = NOW + timedelta(days=7 if row.get('anchor') else 21)
        assert adjusted[row['ledger_identity']]['dueAt'] == (expected.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                                                            if row.get('anchor') else expected.isoformat())
    key = anchors[0]['ledger_identity']
    records[key]['dueAt'] = (NOW + timedelta(days=3)).isoformat()
    assert anchor_floor(rows, records, 7)[key]['dueAt'] == records[key]['dueAt']


def test_mature_anchor_is_due_and_reserved_at_floor():
    cfg = profile(anchor_floor_days=5)
    rows = runtime_curriculum(cfg)
    target = next(r for r in rows if r['id'] == 'cam-on-nha')
    class Seen(EmptyLedger):
        def snapshot(self, *args, **kwargs):
            return {r['ledger_identity']: {'dueAt': '2026-11-01T00:00:00.000Z',
                    'lastHeard': '2026-10-16T00:00:00.000Z' if r == target else NOW.isoformat()}
                    for r in rows}
    ledger = Seen()
    assert not due(cfg, ledger=ledger, now=NOW - timedelta(seconds=1))
    pending = due(cfg, ledger=ledger, now=NOW)
    assert [r['id'] for r in pending] == ['cam-on-nha']
    assert pending[0]['exposure']['dueAt'] == '2026-10-21T00:00:00.000Z'
    plans = [compile('Note.', cfg, ledger=ledger, now=NOW, seed=seed) for seed in range(20)]
    assert any(r.id == 'cam-on-nha' for plan in plans for r in plan.lessons)
