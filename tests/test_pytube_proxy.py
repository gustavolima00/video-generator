import pytest

from src.entities.configs.proxies.youtube import PyTubeYouTubeConfig
from src.proxies import pytube_proxy
from src.proxies.pytube_proxy import PyTubeProxy


class FakeChannel:
    video_urls = [[], []]
    videos_url = "https://www.youtube.com/@FoodieBoyKR/videos"
    shorts_url = "https://www.youtube.com/@FoodieBoyKR/shorts"
    html_url = videos_url
    shorts = [
        type("FakeShort", (), {"video_id": "short123abc"})(),
        type("FakeShort", (), {"video_id": "short456def"})(),
    ]
    initial_data = {
        "contents": [
            {"videoRenderer": {"videoId": "abc123def45"}},
            {
                "richItemRenderer": {
                    "content": {"videoRenderer": {"videoId": "xyz987uvw65"}}
                }
            },
            {"videoRenderer": {"videoId": "abc123def45"}},
        ]
    }

    def __init__(self, url):
        self.url = url


def test_channel_extraction_falls_back_to_initial_data(monkeypatch):
    monkeypatch.setattr(pytube_proxy, "Channel", FakeChannel)

    proxy = PyTubeProxy(PyTubeYouTubeConfig())

    assert proxy._extract_video_ids("https://www.youtube.com/@FoodieBoyKR") == [
        "abc123def45",
        "xyz987uvw65",
    ]


def test_channel_extraction_can_list_shorts_only(monkeypatch):
    monkeypatch.setattr(pytube_proxy, "Channel", FakeChannel)

    proxy = PyTubeProxy(PyTubeYouTubeConfig())

    assert proxy._extract_video_ids(
        "https://www.youtube.com/@FoodieBoyKR",
        surface="shorts",
    ) == [
        "short123abc",
        "short456def",
    ]


class FakeStream:
    resolution = "1080p"

    def download(self, output_path, filename=None):
        import os

        path = os.path.join(output_path, filename or "video.mp4")
        with open(path, "wb") as f:
            f.write(b"mp4-bytes")
        return path


class FakeStreamQuery(list):
    def filter(self, **kwargs):
        return self

    def first(self):
        return self[0] if self else None


class FakeYouTube:
    """Records the client it was built with; optionally fails on stream access."""

    built_with: list[str] = []

    def __init__(self, url, client=None, fail_clients=()):
        self.url = url
        self.client = client
        self.video_id = "abc123def45"
        FakeYouTube.built_with.append(client)
        self._fail_clients = fail_clients

    @property
    def streams(self):
        if self.client in self._fail_clients:
            raise RuntimeError(f"{self.client} was detected as a bot")
        return FakeStreamQuery([FakeStream()])


def _install_fake_youtube(monkeypatch, fail_clients=()):
    FakeYouTube.built_with = []

    def factory(url, client=None):
        return FakeYouTube(url, client=client, fail_clients=fail_clients)

    monkeypatch.setattr(pytube_proxy, "YouTube", factory)


def test_download_never_relies_on_the_pytubefix_default_client(monkeypatch):
    # pytubefix defaults to ANDROID_VR, which YouTube answers with a
    # bot-detection error — the client has to be named on every request.
    _install_fake_youtube(monkeypatch)

    proxy = PyTubeProxy(PyTubeYouTubeConfig())
    assert proxy._download_video_sync("abc123def45") == b"mp4-bytes"

    assert FakeYouTube.built_with == ["WEB"]
    assert None not in FakeYouTube.built_with


def test_download_falls_back_to_the_next_client_when_one_is_blocked(monkeypatch):
    _install_fake_youtube(monkeypatch, fail_clients=("WEB", "MWEB"))

    proxy = PyTubeProxy(PyTubeYouTubeConfig())
    assert proxy._download_video_sync("abc123def45") == b"mp4-bytes"

    assert FakeYouTube.built_with == ["WEB", "MWEB", "WEB_SAFARI"]


def test_download_reports_every_client_failure(monkeypatch):
    import pytest

    _install_fake_youtube(monkeypatch, fail_clients=("WEB", "MWEB", "WEB_SAFARI"))

    proxy = PyTubeProxy(PyTubeYouTubeConfig())
    with pytest.raises(RuntimeError) as excinfo:
        proxy._download_video_sync("abc123def45")

    message = str(excinfo.value)
    for client in ("WEB", "MWEB", "WEB_SAFARI"):
        assert client in message


def test_bot_detected_default_client_is_not_configured():
    assert "ANDROID_VR" not in PyTubeYouTubeConfig().download_clients


class RateLimited(Exception):
    """Stand-in for urllib's HTTPError 429."""
    code = 429

    def __str__(self):
        return "HTTP Error 429: Too Many Requests"


def test_rate_limit_stops_after_one_client_instead_of_trying_all(monkeypatch):
    # A 429 is per-IP, so the other clients can only add load to a limit we
    # have already blown. Trying all three tripled the request volume.
    attempts = []

    def factory(url, client=None):
        attempts.append(client)
        raise RateLimited()

    monkeypatch.setattr(pytube_proxy, "YouTube", factory)
    proxy = PyTubeProxy(PyTubeYouTubeConfig())

    with pytest.raises(pytube_proxy.YouTubeRateLimitError):
        proxy._download_video_sync("abc123def45")

    assert attempts == ["WEB"], f"should stop after the first client, got {attempts}"


def test_non_rate_limit_errors_still_fall_through_every_client(monkeypatch):
    attempts = []

    def factory(url, client=None):
        attempts.append(client)
        raise RuntimeError("video unavailable")

    monkeypatch.setattr(pytube_proxy, "YouTube", factory)
    proxy = PyTubeProxy(PyTubeYouTubeConfig())

    with pytest.raises(RuntimeError) as excinfo:
        proxy._download_video_sync("abc123def45")

    assert not isinstance(excinfo.value, pytube_proxy.YouTubeRateLimitError)
    assert attempts == ["WEB", "MWEB", "WEB_SAFARI"]
