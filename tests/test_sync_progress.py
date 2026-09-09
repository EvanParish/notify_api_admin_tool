import pytest

from app.sync import SyncProgress


def make_recorder():
    calls = []

    async def on_update(done: int, total: int, msg: str):
        calls.append((done, total, msg))

    return calls, on_update


class FakeClock:
    """Deterministic monotonic clock for throttle tests."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_add_total_accumulates():
    _, on_update = make_recorder()
    progress = SyncProgress(on_update)

    progress.add_total(5)
    progress.add_total(3)

    assert progress.total == 8


@pytest.mark.asyncio
async def test_step_increments_done_and_pushes():
    calls, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())
    progress.add_total(2)

    await progress.step("templates")

    assert progress.done == 1
    assert calls == [(1, 2, "templates")]


@pytest.mark.asyncio
async def test_message_pushes_without_incrementing():
    calls, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())
    progress.add_total(2)

    await progress.message("starting")

    assert progress.done == 0
    assert calls == [(0, 2, "starting")]


@pytest.mark.asyncio
async def test_done_never_exceeds_total():
    calls, on_update = make_recorder()
    clock = FakeClock()
    progress = SyncProgress(on_update, time_fn=clock)
    progress.add_total(1)

    await progress.step("a")
    clock.advance(1.0)
    await progress.step("b")

    assert progress.done == 1
    assert calls[-1][0] == 1


@pytest.mark.asyncio
async def test_first_push_is_never_throttled():
    calls, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock(start=0.0))
    progress.add_total(10)

    await progress.message("first")

    assert calls == [(0, 10, "first")]


@pytest.mark.asyncio
async def test_rapid_steps_are_throttled():
    calls, on_update = make_recorder()
    clock = FakeClock()
    progress = SyncProgress(on_update, time_fn=clock)
    progress.add_total(100)

    for i in range(10):
        clock.advance(0.001)
        await progress.step(f"item {i}")

    # First push always fires; the remaining nine fall inside the 100ms window.
    assert len(calls) == 1
    assert progress.done == 10


@pytest.mark.asyncio
async def test_push_resumes_after_throttle_window():
    calls, on_update = make_recorder()
    clock = FakeClock()
    progress = SyncProgress(on_update, time_fn=clock)
    progress.add_total(100)

    await progress.step("a")
    clock.advance(0.001)
    await progress.step("b")
    clock.advance(0.2)
    await progress.step("c")

    assert [c[2] for c in calls] == ["a", "c"]


@pytest.mark.asyncio
async def test_terminal_push_always_fires():
    calls, on_update = make_recorder()
    clock = FakeClock()
    progress = SyncProgress(on_update, time_fn=clock)
    progress.add_total(2)

    await progress.step("a")
    clock.advance(0.001)
    await progress.step("b")

    # Second step completes the work and must push despite the throttle window.
    assert calls[-1] == (2, 2, "b")


@pytest.mark.asyncio
async def test_from_callable_adapts_text_only_sink():
    messages = []

    async def sink(msg: str):
        messages.append(msg)

    progress = SyncProgress.from_callable(sink)
    progress.add_total(1)
    await progress.step("templates")

    assert messages == ["templates"]


@pytest.mark.asyncio
async def test_coerce_passes_through_sync_progress():
    _, on_update = make_recorder()
    progress = SyncProgress(on_update)

    assert SyncProgress.coerce(progress) is progress


@pytest.mark.asyncio
async def test_coerce_wraps_plain_callable():
    messages = []

    async def sink(msg: str):
        messages.append(msg)

    progress = SyncProgress.coerce(sink)

    assert isinstance(progress, SyncProgress)
    await progress.message("hello")
    assert messages == ["hello"]


@pytest.mark.asyncio
async def test_coerce_none_returns_none():
    assert SyncProgress.coerce(None) is None


@pytest.mark.asyncio
async def test_locked_is_false_by_default():
    _, on_update = make_recorder()

    assert SyncProgress(on_update).locked is False


# --- SyncManager integration -------------------------------------------------


@pytest.mark.asyncio
async def test_sync_all_declares_weighted_total(initialized_db):
    """Total is 4 fan-out phases x N services, plus 5 constant-time steps."""
    from app.sync import SyncManager
    from app.repository import list_service_ids

    import tests.testing_data as testing_data
    from tests.test_sync import FakeAPI

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    sync = SyncManager(FakeAPI(), encryption=None)
    await sync.sync_all(progress=progress)

    n = len(await list_service_ids(sync.environment))
    assert n == len(testing_data.service_data["data"])
    assert progress.total == 4 * n + 5
    assert progress.done == progress.total


@pytest.mark.asyncio
async def test_sync_all_locks_progress_against_double_counting(initialized_db):
    from app.sync import SyncManager
    from tests.test_sync import FakeAPI

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    await SyncManager(FakeAPI(), encryption=None).sync_all(progress=progress)

    assert progress.locked is True


@pytest.mark.asyncio
async def test_standalone_fan_out_declares_its_own_total(initialized_db):
    from app.models import Service
    from app.db import get_session
    from app.sync import SyncManager
    from tests.test_sync import FakeAPI

    async with get_session() as session:
        session.add(Service(id="svc-1", name="One", active=True))
        session.add(Service(id="svc-2", name="Two", active=True))
        await session.commit()

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    await SyncManager(FakeAPI()).sync_templates(progress=progress)

    assert progress.total == 2
    assert progress.done == 2


@pytest.mark.asyncio
async def test_locked_fan_out_does_not_add_total(initialized_db):
    from app.models import Service
    from app.db import get_session
    from app.sync import SyncManager
    from tests.test_sync import FakeAPI

    async with get_session() as session:
        session.add(Service(id="svc-1", name="One", active=True))
        await session.commit()

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())
    progress.add_total(50)
    progress.locked = True

    await SyncManager(FakeAPI()).sync_templates(progress=progress)

    assert progress.total == 50
    assert progress.done == 1


@pytest.mark.asyncio
async def test_failing_service_still_counts_one_unit(initialized_db):
    from app.models import Service
    from app.db import get_session
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI

    async with get_session() as session:
        session.add(Service(id="svc-boom", name="Boom", active=True))
        await session.commit()

    api = MockNotificationAPI()

    async def boom(service_id: str):
        raise RuntimeError("upstream exploded")

    api.get_templates = boom

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    result = await SyncManager(api).sync_templates(progress=progress)

    assert result.error_count == 1
    assert progress.done == progress.total == 1


@pytest.mark.asyncio
async def test_404_fast_path_still_counts_one_unit(initialized_db):
    from app.models import Service
    from app.db import get_session
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI

    async with get_session() as session:
        session.add(Service(id="svc-404", name="No Keys", active=True))
        await session.commit()

    api = MockNotificationAPI()

    async def raise_404(service_id, include_revoked=False):
        raise Exception("Client error '404 NOT FOUND'")

    api.get_api_keys = raise_404

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    result = await SyncManager(api).sync_api_keys(progress=progress)

    assert result.error_count == 0
    assert progress.done == progress.total == 1


@pytest.mark.asyncio
async def test_sync_manager_accepts_plain_callable(initialized_db):
    """Back-compat: a bare async text sink still works."""
    from app.sync import SyncManager
    from tests.test_sync import FakeAPI

    messages = []

    async def sink(msg: str):
        messages.append(msg)

    await SyncManager(FakeAPI()).sync_services(progress=sink)

    # Constant-time phases announce on entry and again on completion.
    assert messages == ["services", "services"]


# --- phase tracking ----------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_context_sets_current_phase():
    from app.sync import PHASE_API_KEYS

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    assert progress.current_phase() is None
    async with progress.phase(PHASE_API_KEYS):
        assert progress.current_phase() == PHASE_API_KEYS
    assert progress.current_phase() is None


@pytest.mark.asyncio
async def test_entering_a_phase_pushes_immediately():
    from app.sync import PHASE_TEMPLATES

    calls, on_update = make_recorder()
    clock = FakeClock()
    progress = SyncProgress(on_update, time_fn=clock)
    progress.add_total(10)

    await progress.step("services")
    # Well inside the throttle window: a phase change must still be shown,
    # otherwise the label names a phase that already finished.
    clock.advance(0.001)
    async with progress.phase(PHASE_TEMPLATES):
        pass

    assert calls[1] == (1, 10, PHASE_TEMPLATES)


@pytest.mark.asyncio
async def test_current_phase_is_the_least_advanced_of_concurrent_phases():
    from app.sync import PHASE_API_KEYS, PHASE_CALLBACKS

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    # Two environments running concurrently at different points in the run.
    async with progress.phase(PHASE_CALLBACKS):
        async with progress.phase(PHASE_API_KEYS):
            assert progress.current_phase() == PHASE_API_KEYS


@pytest.mark.asyncio
async def test_leaving_the_least_advanced_phase_advances_the_label():
    from app.sync import PHASE_API_KEYS, PHASE_CALLBACKS

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    async with progress.phase(PHASE_CALLBACKS):
        async with progress.phase(PHASE_API_KEYS):
            pass
        assert progress.current_phase() == PHASE_CALLBACKS


@pytest.mark.asyncio
async def test_same_phase_entered_by_two_environments_is_refcounted():
    from app.sync import PHASE_TEMPLATES

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    async with progress.phase(PHASE_TEMPLATES):
        async with progress.phase(PHASE_TEMPLATES):
            pass
        assert progress.current_phase() == PHASE_TEMPLATES
    assert progress.current_phase() is None


@pytest.mark.asyncio
async def test_phase_is_exited_when_the_body_raises():
    from app.sync import PHASE_USERS

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    with pytest.raises(RuntimeError):
        async with progress.phase(PHASE_USERS):
            raise RuntimeError("boom")

    assert progress.current_phase() is None


@pytest.mark.asyncio
async def test_step_without_message_reports_the_current_phase():
    from app.sync import PHASE_SMS_SENDERS

    calls, on_update = make_recorder()
    clock = FakeClock()
    progress = SyncProgress(on_update, time_fn=clock)
    progress.add_total(5)

    async with progress.phase(PHASE_SMS_SENDERS):
        clock.advance(1.0)
        await progress.step()

    assert calls[-1][2] == PHASE_SMS_SENDERS


@pytest.mark.asyncio
async def test_step_outside_any_phase_reuses_the_last_message():
    calls, on_update = make_recorder()
    clock = FakeClock()
    progress = SyncProgress(on_update, time_fn=clock)
    progress.add_total(5)

    await progress.message("Syncing all data for 2 environment(s)...")
    clock.advance(1.0)
    await progress.step()

    assert calls[-1][2] == "Syncing all data for 2 environment(s)..."


@pytest.mark.asyncio
async def test_sync_all_labels_phases_in_order(initialized_db):
    from app.sync import PHASE_ORDER, SyncManager
    from tests.test_sync import FakeAPI

    calls, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    await SyncManager(FakeAPI(), encryption=None).sync_all(progress=progress)

    labels = [msg for _, _, msg in calls]
    assert labels, "expected progress updates"
    assert all(label in PHASE_ORDER for label in labels)
    seen_indexes = [PHASE_ORDER.index(label) for label in labels]
    assert seen_indexes == sorted(seen_indexes)


@pytest.mark.asyncio
async def test_fan_out_phase_is_announced_before_its_work_completes(initialized_db):
    """The label must name the phase in flight, not the last one that finished.

    Without an entry announcement the label still reads "services" while the
    first (possibly very slow) template request is in flight.
    """
    from app.sync import PHASE_TEMPLATES, SyncManager
    from tests.test_sync import FakeAPI

    calls, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    await SyncManager(FakeAPI(), encryption=None).sync_all(progress=progress)

    first_templates_push = next(c for c in calls if c[2] == PHASE_TEMPLATES)
    # Only the single services unit has completed at this point.
    assert first_templates_push[0] == 1


@pytest.mark.asyncio
async def test_slow_fan_out_reports_its_own_phase_not_the_previous_one(initialized_db):
    """A phase that hangs must be named while it hangs."""
    import asyncio

    from app.models import Service
    from app.db import get_session
    from app.sync import PHASE_TEMPLATES, SyncManager
    from app.api_client import MockNotificationAPI

    async with get_session() as session:
        session.add(Service(id="svc-slow", name="Slow", active=True))
        await session.commit()

    labels_during_hang = []
    release = asyncio.Event()

    api = MockNotificationAPI()

    async def hang(service_id: str):
        labels_during_hang.append(progress._last_message)
        await release.wait()
        return []

    api.get_templates = hang

    _, on_update = make_recorder()
    progress = SyncProgress(on_update, time_fn=FakeClock())

    task = asyncio.create_task(SyncManager(api).sync_templates(progress=progress))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    release.set()
    await task

    assert labels_during_hang == [PHASE_TEMPLATES]
