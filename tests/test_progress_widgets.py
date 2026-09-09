from unittest.mock import MagicMock

import pytest

from app.ui import state as _st


class FakeWidget:
    def __init__(self):
        self.value = None
        self.text = None
        self.visible = None

    def set_visibility(self, visible: bool) -> None:
        self.visible = visible


def make_client(client_id: str, connected: bool = True):
    client = MagicMock()
    client.id = client_id
    client.has_socket_connection = connected
    return client


@pytest.fixture(autouse=True)
def clean_registry():
    _st.clear_progress_widgets()
    yield
    _st.clear_progress_widgets()


def register(monkeypatch, client, bar, label):
    monkeypatch.setattr(type(_st.context), "client", property(lambda self: client))
    _st.register_progress_widgets(bar, label)


def install_clients(monkeypatch, *clients):
    monkeypatch.setattr(_st.Client, "instances", {c.id: c for c in clients})


def test_register_stores_widgets(monkeypatch):
    bar, label = FakeWidget(), FakeWidget()
    register(monkeypatch, make_client("a"), bar, label)

    assert _st.progress_widget_count() == 1


def test_disconnect_removes_widgets(monkeypatch):
    client = make_client("a")
    register(monkeypatch, client, FakeWidget(), FakeWidget())

    # NiceGUI invokes the handler registered via Client.on_disconnect.
    handler = client.on_disconnect.call_args[0][0]
    handler()

    assert _st.progress_widget_count() == 0


@pytest.mark.asyncio
async def test_push_updates_every_registered_client(monkeypatch):
    a, b = make_client("a"), make_client("b")
    bar_a, label_a = FakeWidget(), FakeWidget()
    bar_b, label_b = FakeWidget(), FakeWidget()
    register(monkeypatch, a, bar_a, label_a)
    register(monkeypatch, b, bar_b, label_b)
    install_clients(monkeypatch, a, b)

    await _st.push_progress(3, 12, "[dev] templates")

    for bar, label in ((bar_a, label_a), (bar_b, label_b)):
        assert bar.value == pytest.approx(0.25)
        assert bar.visible is True
        assert label.text == "[dev] templates - 3/12"


@pytest.mark.asyncio
async def test_push_with_zero_total_hides_bar(monkeypatch):
    client = make_client("a")
    bar, label = FakeWidget(), FakeWidget()
    register(monkeypatch, client, bar, label)
    install_clients(monkeypatch, client)

    await _st.push_progress(0, 0, "starting")

    assert bar.visible is False
    assert bar.value == 0.0
    assert label.text == "starting"


@pytest.mark.asyncio
async def test_disconnected_client_is_swept(monkeypatch):
    client = make_client("a", connected=False)
    register(monkeypatch, client, FakeWidget(), FakeWidget())
    install_clients(monkeypatch, client)

    await _st.push_progress(1, 2, "x")

    assert _st.progress_widget_count() == 0


@pytest.mark.asyncio
async def test_vanished_client_is_swept(monkeypatch):
    client = make_client("a")
    register(monkeypatch, client, FakeWidget(), FakeWidget())
    install_clients(monkeypatch)  # client no longer in Client.instances

    await _st.push_progress(1, 2, "x")

    assert _st.progress_widget_count() == 0


@pytest.mark.asyncio
async def test_failing_widget_is_swept_without_blocking_others(monkeypatch):
    class ExplodingWidget(FakeWidget):
        def set_visibility(self, visible: bool) -> None:
            raise RuntimeError("client is gone")

    bad, good = make_client("bad"), make_client("good")
    good_bar, good_label = FakeWidget(), FakeWidget()
    register(monkeypatch, bad, ExplodingWidget(), FakeWidget())
    register(monkeypatch, good, good_bar, good_label)
    install_clients(monkeypatch, bad, good)

    await _st.push_progress(1, 2, "x")

    assert _st.progress_widget_count() == 1
    assert good_bar.value == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_push_records_latest_message_on_state(monkeypatch):
    client = make_client("a")
    register(monkeypatch, client, FakeWidget(), FakeWidget())
    install_clients(monkeypatch, client)

    await _st.push_progress(2, 4, "[perf] users")

    assert _st.state.sync_message == "[perf] users - 2/4"


@pytest.mark.asyncio
async def test_hide_progress_hides_every_bar(monkeypatch):
    a, b = make_client("a"), make_client("b")
    bar_a, bar_b = FakeWidget(), FakeWidget()
    register(monkeypatch, a, bar_a, FakeWidget())
    register(monkeypatch, b, bar_b, FakeWidget())
    install_clients(monkeypatch, a, b)

    await _st.push_progress(1, 2, "x")
    _st.hide_progress()

    assert bar_a.visible is False
    assert bar_b.visible is False


def test_set_progress_text_broadcasts(monkeypatch):
    a, b = make_client("a"), make_client("b")
    label_a, label_b = FakeWidget(), FakeWidget()
    register(monkeypatch, a, FakeWidget(), label_a)
    register(monkeypatch, b, FakeWidget(), label_b)
    install_clients(monkeypatch, a, b)

    _st.set_progress_text("Sync complete")

    assert label_a.text == "Sync complete"
    assert label_b.text == "Sync complete"
