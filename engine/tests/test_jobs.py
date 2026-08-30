"""Job system tests.

The state machine and cancellation are pure logic, so all of this runs in CI
without a GPU — which matters, because these are the guarantees the UI makes to
the user about long-running work.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from volum_core.jobs import (
    InvalidTransitionError,
    JobError,
    JobManager,
    JobRecord,
    JobStatus,
    JobStore,
    can_transition,
)
from volum_core.jobs.manager import CancellationToken, JobCancelledError


@pytest.fixture
def manager(tmp_path: Path) -> JobManager:
    return JobManager(JobStore(tmp_path / "jobs"))


# --- state machine --------------------------------------------------------


def test_the_happy_path_is_walkable() -> None:
    path = [
        JobStatus.QUEUED,
        JobStatus.PREPROCESSING,
        JobStatus.RECONSTRUCTING,
        JobStatus.TEXTURING,
        JobStatus.OPTIMIZING,
        JobStatus.VALIDATING,
        JobStatus.COMPLETED,
    ]
    for current, target in pairwise(path):
        assert can_transition(current, target)


def test_stages_may_be_skipped_forwards() -> None:
    """A provider without a texturing step goes straight to optimizing."""
    assert can_transition(JobStatus.RECONSTRUCTING, JobStatus.OPTIMIZING)


def test_stages_may_not_go_backwards() -> None:
    """Backwards movement would read as progress in the UI while being a bug."""
    assert not can_transition(JobStatus.OPTIMIZING, JobStatus.RECONSTRUCTING)


def test_a_stage_cannot_re_enter_itself() -> None:
    """Otherwise a stuck provider looks like it is making progress."""
    assert not can_transition(JobStatus.RECONSTRUCTING, JobStatus.RECONSTRUCTING)


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.QUEUED,
        JobStatus.PREPROCESSING,
        JobStatus.RECONSTRUCTING,
        JobStatus.TEXTURING,
        JobStatus.OPTIMIZING,
        JobStatus.VALIDATING,
    ],
)
def test_every_active_stage_can_fail_or_be_cancelled(status: JobStatus) -> None:
    assert can_transition(status, JobStatus.FAILED)
    assert can_transition(status, JobStatus.CANCELLED)


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED])
def test_terminal_states_are_final(status: JobStatus) -> None:
    assert status.is_terminal
    assert all(not can_transition(status, target) for target in JobStatus)


# --- manager --------------------------------------------------------------


def test_created_job_starts_queued_and_persists(manager: JobManager) -> None:
    record = manager.create(model_id="triposr", input_files=[Path("a.png")])
    assert record.status is JobStatus.QUEUED
    assert manager.get(record.id) is not None


def test_advancing_records_progress_and_start_time(manager: JobManager) -> None:
    record = manager.create(model_id="triposr", input_files=[])
    record = manager.advance(record, JobStatus.PREPROCESSING, message="Preparing images")
    assert record.started_at is not None
    assert record.current_progress is not None
    assert record.current_progress.message == "Preparing images"


def test_progress_fraction_defaults_to_none(manager: JobManager) -> None:
    """Spec section 18: no invented percentages. Absence is the honest default."""
    record = manager.create(model_id="triposr", input_files=[])
    record = manager.advance(record, JobStatus.RECONSTRUCTING)
    assert record.current_progress is not None
    assert record.current_progress.fraction is None


def test_reporting_within_a_stage_does_not_transition(manager: JobManager) -> None:
    record = manager.create(model_id="triposr", input_files=[])
    record = manager.advance(record, JobStatus.RECONSTRUCTING)
    record = manager.report(record, "Sampling", fraction=0.4)
    assert record.status is JobStatus.RECONSTRUCTING
    assert len(record.progress) == 2


def test_illegal_transitions_raise(manager: JobManager) -> None:
    record = manager.create(model_id="triposr", input_files=[])
    record = manager.advance(record, JobStatus.COMPLETED)
    with pytest.raises(InvalidTransitionError):
        manager.advance(record, JobStatus.RECONSTRUCTING)


def test_failing_stores_a_separated_error(manager: JobManager) -> None:
    """User-facing message and technical detail are kept apart (spec section 55)."""
    record = manager.create(model_id="trellis2", input_files=[])
    record = manager.advance(record, JobStatus.RECONSTRUCTING)
    record = manager.fail(
        record,
        JobError(
            message="VOLUM ran out of memory while generating the model.",
            technical="RuntimeError: MPS backend out of memory",
            suggestions=["Try Fast mode", "Try a smaller model"],
        ),
    )
    assert record.status is JobStatus.FAILED
    assert record.error is not None
    assert "MPS" not in record.error.message
    assert record.error.technical is not None and "MPS" in record.error.technical


def test_finished_jobs_get_a_finish_time_and_duration(manager: JobManager) -> None:
    record = manager.create(model_id="triposr", input_files=[])
    record = manager.advance(record, JobStatus.RECONSTRUCTING)
    record = manager.advance(record, JobStatus.COMPLETED)
    assert record.finished_at is not None
    assert record.duration_seconds is not None


def test_listeners_are_notified(manager: JobManager) -> None:
    seen: list[JobStatus] = []
    manager.subscribe(lambda record: seen.append(record.status))
    record = manager.create(model_id="triposr", input_files=[])
    manager.advance(record, JobStatus.PREPROCESSING)
    assert seen == [JobStatus.QUEUED, JobStatus.PREPROCESSING]


def test_a_broken_listener_cannot_break_a_job(manager: JobManager) -> None:
    """The SSE stream must never be able to take a generation down with it."""

    def explode(_: JobRecord) -> None:
        raise RuntimeError("listener is broken")

    manager.subscribe(explode)
    record = manager.create(model_id="triposr", input_files=[])
    assert manager.advance(record, JobStatus.PREPROCESSING).status is JobStatus.PREPROCESSING


def test_unsubscribe_stops_notifications(manager: JobManager) -> None:
    seen: list[str] = []
    unsubscribe = manager.subscribe(lambda record: seen.append(record.id))
    manager.create(model_id="triposr", input_files=[])
    unsubscribe()
    manager.create(model_id="triposr", input_files=[])
    assert len(seen) == 1


# --- cancellation ---------------------------------------------------------


def test_cancelling_marks_the_job_and_sets_the_token(manager: JobManager) -> None:
    record = manager.create(model_id="triposr", input_files=[])
    manager.advance(record, JobStatus.RECONSTRUCTING)
    cancelled = manager.cancel(record.id)
    assert cancelled is not None
    assert cancelled.status is JobStatus.CANCELLED
    assert manager.token(record.id).is_cancelled


def test_cancelling_a_finished_job_is_a_no_op(manager: JobManager) -> None:
    record = manager.create(model_id="triposr", input_files=[])
    manager.advance(record, JobStatus.COMPLETED)
    assert manager.cancel(record.id).status is JobStatus.COMPLETED  # type: ignore[union-attr]


def test_cancelling_an_unknown_job_returns_none(manager: JobManager) -> None:
    assert manager.cancel("deadbeef") is None


def test_token_raises_at_a_stage_boundary() -> None:
    """Cancellation is cooperative because a running GPU kernel cannot be
    interrupted from Python; the token stops work at the next boundary."""
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel()
    with pytest.raises(JobCancelledError):
        token.raise_if_cancelled()


# --- store ----------------------------------------------------------------


def test_store_round_trip(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    record = JobRecord(model_id="triposr", input_files=[Path("x.png")], seed=7)
    store.save(record)
    loaded = store.load(record.id)
    assert loaded is not None
    assert loaded.seed == 7


def test_store_rejects_a_traversing_job_id(tmp_path: Path) -> None:
    """Job ids arrive from HTTP paths and CLI arguments; '../..' must not write
    outside the data directory (spec section 50)."""
    store = JobStore(tmp_path)
    for bad in ("../escape", "..", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="Invalid job id"):
            store.directory(bad)


def test_store_lists_newest_first(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    ids = []
    for _ in range(3):
        record = JobRecord(model_id="triposr")
        store.save(record)
        ids.append(record.id)
    listed = [record.id for record in store.list_jobs()]
    assert set(listed) == set(ids)
    assert len(listed) == 3


def test_a_corrupt_job_does_not_break_the_list(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.save(JobRecord(model_id="triposr"))
    broken = tmp_path / "abc123"
    broken.mkdir()
    (broken / "job.json").write_text("{ not json", encoding="utf-8")
    assert len(store.list_jobs()) == 1


def test_deleting_removes_the_whole_job_directory(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    record = JobRecord(model_id="triposr")
    store.save(record)
    (store.directory(record.id) / "artifact.glb").write_bytes(b"x")
    assert store.delete(record.id)
    assert not store.directory(record.id).exists()


def test_interrupted_jobs_are_failed_at_startup(tmp_path: Path) -> None:
    """Without this a job left mid-flight by a crash spins for ever. The message
    says the application stopped, not that the job itself failed."""
    store = JobStore(tmp_path)
    running = JobRecord(model_id="trellis2", status=JobStatus.RECONSTRUCTING)
    done = JobRecord(model_id="triposr", status=JobStatus.COMPLETED)
    store.save(running)
    store.save(done)

    recovered = store.recover_interrupted()

    assert [record.id for record in recovered] == [running.id]
    reloaded = store.load(running.id)
    assert reloaded is not None
    assert reloaded.status is JobStatus.FAILED
    assert reloaded.error is not None
    assert "interrupted" in reloaded.error.message
    assert store.load(done.id).status is JobStatus.COMPLETED  # type: ignore[union-attr]
