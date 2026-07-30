import json
from json import JSONDecodeError
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.proxies.tiktok_publisher_proxy as proxy_module
from src.proxies.tiktok_publisher_proxy import (
    BrowserUseTikTokPublisherProxy,
    _LLMFailureRecorder,
)


class _FakeDoneAction:
    def model_dump(self, exclude_none=True):
        return {"done": {"success": True}}


class _FakeHistoryStep:
    def __init__(self):
        self.model_output = SimpleNamespace(action=[_FakeDoneAction()])
        self.state = SimpleNamespace(url="https://www.tiktok.com/tiktokstudio/manage")
        self.errors = []


class _FakeHistory:
    def __init__(self):
        self.history = [_FakeHistoryStep()]

    def final_result(self):
        return "✅ Video successfully scheduled on TikTok Studio!"


class _FakeAgent:
    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.browser_session = SimpleNamespace(start=self._start)

    async def _start(self):
        return None

    async def run(self, max_steps):
        return _FakeHistory()


class _FakeBrowser:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeRawResponse:
    def __init__(
        self,
        *,
        body,
        parsed=None,
        parse_exception=None,
        headers=None,
        status_code=502,
        url="https://openrouter.ai/api/v1/chat/completions",
        request_id="req_test",
    ):
        self.text = body
        self.headers = headers or {}
        self.status_code = status_code
        self.http_request = SimpleNamespace(url=url)
        self.request_id = request_id
        self.http_response = object()
        self._parsed = parsed
        self._parse_exception = parse_exception

    def parse(self):
        if self._parse_exception is not None:
            raise self._parse_exception
        return self._parsed


def _make_proxy(tmp_path: Path, **kwargs) -> BrowserUseTikTokPublisherProxy:
    return BrowserUseTikTokPublisherProxy(
        openrouter_api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        cookies_path=str(tmp_path / "tiktok_cookies.json"),
        **kwargs,
    )


def test_format_description_strips_old_hashtags_and_dedupes_new_tags():
    result = BrowserUseTikTokPublisherProxy._format_description(
        "Meu chefe me proibiu de decidir  #fyp #storytime",
        ["#reddit", "#fyp", "chefeToxico#reddit", "obedienciaCega"],
    )

    assert result == (
        "Meu chefe me proibiu de decidir  #reddit #fyp #chefeToxico"
    )


def test_format_description_without_hashtags_does_not_add_defaults():
    result = BrowserUseTikTokPublisherProxy._format_description("caption", None)

    assert result == "caption"


@pytest.mark.asyncio
async def test_publish_video_builds_agent_with_thinking_disabled(
    monkeypatch, tmp_path: Path
):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"mp4")

    proxy = _make_proxy(tmp_path)

    monkeypatch.setattr(proxy_module, "Browser", _FakeBrowser)
    monkeypatch.setattr(proxy_module, "ChatOpenAI", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(proxy_module, "Agent", _FakeAgent)
    monkeypatch.setattr(proxy_module, "build_tools", lambda: object())
    monkeypatch.setattr(
        proxy, "_start_session_with_stealth", lambda agent: proxy_module.asyncio.sleep(0)
    )
    monkeypatch.setattr(proxy, "_safe_stop", lambda browser: proxy_module.asyncio.sleep(0))

    result = await proxy.publish_video(
        video_path=str(video_path),
        description="caption",
    )

    assert result == "✅ Video successfully scheduled on TikTok Studio!"
    assert _FakeAgent.last_kwargs["use_thinking"] is False


@pytest.mark.asyncio
async def test_step_callback_handles_missing_thinking(tmp_path: Path):
    proxy = _make_proxy(tmp_path)
    appended = []
    proxy._memory.append_live_step = appended.append

    callback = proxy._make_step_callback()
    model_output = SimpleNamespace(
        evaluation_previous_goal="ok",
        memory="mem",
        next_goal="goal",
        action=[],
    )

    await callback(SimpleNamespace(url="https://example.com"), model_output, 3)

    assert appended == [
        {
            "step": 3,
            "url": "https://example.com",
            "thinking": "",
            "eval": "ok",
            "memory": "mem",
            "next_goal": "goal",
            "actions_planned": [],
        }
    ]


def _make_recorder(proxy: BrowserUseTikTokPublisherProxy, tmp_path: Path, **kwargs):
    return _LLMFailureRecorder(
        runs_dir=tmp_path,
        run_ts="20260515T123456",
        model="deepseek/deepseek-v4-flash",
        max_body_chars=kwargs.pop("max_body_chars", 65536),
        enabled=kwargs.pop("enabled", True),
        memory=proxy._memory,
        logger=proxy._logger,
    )


@pytest.mark.asyncio
async def test_raw_capture_writes_artifact_for_outer_provider_parse_failure(
    monkeypatch, tmp_path: Path
):
    proxy = _make_proxy(tmp_path)
    recorder = _make_recorder(proxy, tmp_path)

    bad_body = (
        "Authorization: Bearer super-secret-token\n"
        "api_key=abc123\n"
        "<html>gateway error</html>"
    )
    raw_response = _FakeRawResponse(
        body=bad_body,
        headers={"set-cookie": "sessionid=top-secret", "content-type": "text/html"},
        parse_exception=JSONDecodeError("Expecting value", "not json", 0),
    )

    async def fake_create(async_self, *args, **kwargs):
        return raw_response

    monkeypatch.setattr(proxy_module.AsyncCompletions, "create", fake_create)

    with proxy._capture_openai_raw_failures(recorder):
        with pytest.raises(JSONDecodeError):
            await proxy_module.AsyncCompletions.create(object(), model="test-model")

    artifact_path = tmp_path / "20260515T123456.llm_failure.json"
    assert artifact_path.exists()

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["parse_stage"] == "provider_http_parse"
    assert payload["model"] == "test-model"
    assert payload["response_headers"]["set-cookie"] == "***REDACTED***"
    assert "***REDACTED***" in payload["raw_body"]
    assert "super-secret-token" not in payload["raw_body"]
    assert "abc123" not in payload["raw_body"]


@pytest.mark.asyncio
async def test_raw_capture_writes_artifact_for_truncated_structured_output(
    monkeypatch, tmp_path: Path
):
    proxy = _make_proxy(tmp_path)
    recorder = _make_recorder(proxy, tmp_path)

    message_content = '{"thinking": "Let me think"'
    parsed = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=message_content))]
    )
    raw_response = _FakeRawResponse(
        body='{"choices":[{"message":{"content":"{\\"thinking\\": \\"Let me think\\""}}]}',
        parsed=parsed,
        headers={"content-type": "application/json"},
        status_code=200,
    )

    async def fake_create(async_self, *args, **kwargs):
        return raw_response

    monkeypatch.setattr(proxy_module.AsyncCompletions, "create", fake_create)

    with proxy._capture_openai_raw_failures(recorder):
        result = await proxy_module.AsyncCompletions.create(object(), model="test-model")

    artifact_path = tmp_path / "20260515T123456.llm_failure.json"
    assert result is parsed
    assert artifact_path.exists()

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["parse_stage"] == "structured_output_json"
    assert payload["message_content"] == message_content


@pytest.mark.asyncio
async def test_raw_capture_does_not_write_artifact_on_success(
    monkeypatch, tmp_path: Path
):
    proxy = _make_proxy(tmp_path)
    recorder = _make_recorder(proxy, tmp_path)

    message_content = json.dumps(
        {
            "evaluation_previous_goal": "success",
            "memory": "done",
            "next_goal": "finish",
            "action": [{"done": {"success": True}}],
        }
    )
    parsed = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=message_content))]
    )
    raw_response = _FakeRawResponse(
        body='{"choices":[{"message":{"content":"valid"}}]}',
        parsed=parsed,
        headers={"content-type": "application/json"},
        status_code=200,
    )

    async def fake_create(async_self, *args, **kwargs):
        return raw_response

    monkeypatch.setattr(proxy_module.AsyncCompletions, "create", fake_create)

    with proxy._capture_openai_raw_failures(recorder):
        result = await proxy_module.AsyncCompletions.create(object(), model="test-model")

    assert result is parsed
    assert not (tmp_path / "20260515T123456.llm_failure.json").exists()
