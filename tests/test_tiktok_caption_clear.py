"""The CDP clear must ask Blink for real editing commands.

A bare Ctrl+A / Delete key event does nothing useful in a contenteditable:
select-all normally comes from the browser's own shortcut handling, so
without ``commands`` the Delete just ate a single character and left the
prefilled file name behind.
"""

import pytest

from src.proxies.tiktok_publisher_tools import _cdp_clear_field

SELECTOR = "div[contenteditable='true'][role='combobox']"


class _FakeInput:
    def __init__(self, recorder):
        self._recorder = recorder

    async def dispatchKeyEvent(self, params=None, session_id=None):
        self._recorder.append(params)
        return {}


class _FakeRuntime:
    def __init__(self, session):
        self._session = session

    async def evaluate(self, params=None, session_id=None):
        expression = params["expression"]
        if "el.focus()" in expression:
            return {"result": {"value": self._session.focusable}}
        # the "is it empty now?" probe
        return {"result": {"value": self._session.empty}}


class _FakeSend:
    def __init__(self, session):
        self.Input = _FakeInput(session.key_events)
        self.Runtime = _FakeRuntime(session)


class _FakeSession:
    """Minimal stand-in for browser-use's CDP session."""

    def __init__(self, *, focusable=True, empty=True):
        self.focusable = focusable
        self.empty = empty
        self.key_events = []
        self.cdp_client = type("C", (), {"send": _FakeSend(self)})()
        self.session_id = "s1"

    async def get_or_create_cdp_session(self):
        return self


@pytest.mark.asyncio
async def test_cdp_clear_issues_select_all_and_delete_commands():
    session = _FakeSession(empty=True)

    assert await _cdp_clear_field(session, SELECTOR) is True

    commands = [c for ev in session.key_events for c in ev.get("commands", [])]
    assert commands == ["selectAll", "deleteBackward"]
    # Ctrl on the first pass (the publisher runs on Linux).
    select_all = next(ev for ev in session.key_events if "selectAll" in ev.get("commands", []))
    assert select_all["modifiers"] == 2
    assert select_all["type"] == "rawKeyDown"


@pytest.mark.asyncio
async def test_cdp_clear_retries_with_the_meta_modifier():
    session = _FakeSession(empty=False)

    assert await _cdp_clear_field(session, SELECTOR) is False

    modifiers = {
        ev["modifiers"]
        for ev in session.key_events
        if "selectAll" in ev.get("commands", [])
    }
    assert modifiers == {2, 4}  # Ctrl, then Cmd for macOS


@pytest.mark.asyncio
async def test_cdp_clear_gives_up_when_the_field_is_gone():
    session = _FakeSession(focusable=False)

    assert await _cdp_clear_field(session, SELECTOR) is False
    assert session.key_events == []
