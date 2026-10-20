import multiprocessing
import sqlite3
from dataclasses import replace

from better_speech import Ledger, compile, record_delivered
from tests.test_model_free_audio import profile


def executed_plan(directory, learner="liam"):
    return replace(compile("Source.", profile(state_dir=str(directory), learner_id=learner), ledger=Ledger.for_profile(profile(state_dir=str(directory), learner_id=learner))), executed=True)


def writer(path, plan, worker_id, start):
    start.wait(10)
    ledger = Ledger(path, busy_timeout_s=10)
    for index in range(8):
        # All workers share these ids and also write their own ids.
        for identity in (f"shared-{index}", f"{worker_id}-{index}"):
            result = record_delivered(plan, identity, "2026-10-19T00:00:00Z", ledger=ledger)
            if result.diagnostics:
                raise RuntimeError(result.diagnostics)


def test_multiprocessing_writers_never_lose_increments(tmp_path):
    context = multiprocessing.get_context("spawn")
    plan = executed_plan(tmp_path)
    path = tmp_path / "concurrent.sqlite3"
    start = context.Event()
    processes = [context.Process(target=writer, args=(path, plan, i, start)) for i in range(4)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    state = Ledger(path).snapshot()
    assert state[plan.lessons[0].phrase_key]["exposureCount"] == 8 + 4 * 8
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 40


def test_out_of_order_delivery_replay_is_identical(tmp_path):
    plan = executed_plan(tmp_path)
    dates = [
        "2026-10-01T00:00:00Z",
        "2026-10-02T00:00:00Z",
        "2026-10-03T00:00:00Z",
        "2026-10-05T00:00:00Z",
        "2026-10-12T00:00:00Z",
        "2026-11-02T00:00:00Z",
    ]
    forward, reverse = Ledger(tmp_path / "forward.db"), Ledger(tmp_path / "reverse.db")
    for index in range(len(dates)):
        assert record_delivered(plan, str(index), dates[index], ledger=forward).added == 1
    for index in reversed(range(len(dates))):
        assert record_delivered(plan, str(index), dates[index], ledger=reverse).added == 1
    assert forward.snapshot() == reverse.snapshot()
    record = next(iter(forward.snapshot().values()))
    assert record["firstHeard"] == "2026-10-01T00:00:00.000Z"
    assert record["lastHeard"] == "2026-11-02T00:00:00.000Z"
    assert record["intervalIndex"] == 3
    assert record["dueAt"] == "2026-11-23T00:00:00.000Z"
    assert record["exposureCount"] == 6


def test_preparation_duplicates_and_learner_isolation(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    prepared = compile("Source.", profile(state_dir=str(tmp_path)), ledger=Ledger.for_profile(profile(state_dir=str(tmp_path))))
    assert record_delivered(prepared, "not-sent", ledger=ledger).diagnostics
    assert ledger.snapshot() == {}
    plan = replace(prepared, executed=True)
    assert record_delivered(plan, "sent", "2026-10-19T00:00:00Z", ledger=ledger).added == 1
    assert record_delivered(plan, "sent", "2026-11-19T00:00:00Z", ledger=ledger).added == 0
    assert ledger.snapshot("other") == {}
    plain = replace(plan, lessons=(), clips=plan.fallback_clips)
    assert record_delivered(plain, "english", ledger=ledger).added == 0
    assert next(iter(ledger.snapshot().values()))["exposureCount"] == 1


def test_sixteen_concurrent_first_time_writers(tmp_path):
    context = multiprocessing.get_context('spawn')
    plan = executed_plan(tmp_path)
    path = tmp_path / 'fresh.sqlite3'
    start = context.Event()
    processes = [context.Process(target=writer, args=(path, plan, i, start)) for i in range(16)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    assert Ledger(path).snapshot()[plan.lessons[0].phrase_key]['exposureCount'] == 8 + 16 * 8
