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
    """Records how it was built; optionally fails on stream access."""

    built_with: list[str] = []
    built_kwargs: list[dict] = []

    def __init__(self, url, client=None, fail_clients=(), **kwargs):
        self.url = url
        self.client = client
        self.video_id = "abc123def45"
        FakeYouTube.built_with.append(client)
        FakeYouTube.built_kwargs.append(kwargs)
        self._fail_clients = fail_clients

    @property
    def streams(self):
        if self.client in self._fail_clients:
            raise RuntimeError(f"{self.client} was detected as a bot")
        return FakeStreamQuery([FakeStream()])


def _install_fake_youtube(monkeypatch, fail_clients=()):
    FakeYouTube.built_with = []
    FakeYouTube.built_kwargs = []

    def factory(url, client=None, **kwargs):
        return FakeYouTube(url, client=client, fail_clients=fail_clients, **kwargs)

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


def _token_config(po_token="po-token-value", visitor_data="visitor-data-value"):
    return PyTubeYouTubeConfig(po_token=po_token, visitor_data=visitor_data)


def test_configured_token_is_handed_to_pytubefix(monkeypatch):
    # pytubefix otherwise mints its own poToken through botGuard, and that
    # synthetic token is what YouTube answers with 429 — the operator's token
    # only takes precedence when the verifier is wired in.
    _install_fake_youtube(monkeypatch)

    proxy = PyTubeProxy(_token_config())
    assert proxy._download_video_sync("abc123def45") == b"mp4-bytes"

    kwargs = FakeYouTube.built_kwargs[0]
    assert kwargs["use_po_token"] is True
    assert callable(kwargs["po_token_verifier"])


def test_verifier_returns_visitor_data_before_the_token(monkeypatch):
    # pytubefix unpacks the verifier as (visitorData, po_token); swapping them
    # sends the token as a visitor id and the request is rejected.
    _install_fake_youtube(monkeypatch)

    proxy = PyTubeProxy(_token_config(po_token="THE-TOKEN", visitor_data="THE-VISITOR"))
    proxy._download_video_sync("abc123def45")

    verifier = FakeYouTube.built_kwargs[0]["po_token_verifier"]
    assert verifier() == ("THE-VISITOR", "THE-TOKEN")


def test_download_without_a_token_builds_youtube_exactly_as_before(monkeypatch):
    _install_fake_youtube(monkeypatch)

    proxy = PyTubeProxy(PyTubeYouTubeConfig())
    assert proxy._download_video_sync("abc123def45") == b"mp4-bytes"

    assert FakeYouTube.built_kwargs == [{}]


def test_blank_token_counts_as_no_token(monkeypatch):
    # An operator who empties the .env values leaves them as empty strings,
    # which must mean "off" rather than "send an empty token".
    _install_fake_youtube(monkeypatch)

    proxy = PyTubeProxy(_token_config(po_token="   ", visitor_data=""))
    assert proxy._download_video_sync("abc123def45") == b"mp4-bytes"

    assert FakeYouTube.built_kwargs == [{}]


@pytest.mark.parametrize(
    "config",
    [
        _token_config(visitor_data=None),
        _token_config(po_token=None),
        _token_config(visitor_data="  "),
    ],
)
def test_half_a_token_pair_fails_at_construction(config):
    # pytubefix needs both halves; half a pair is an operator mistake, and
    # discovering it on the first download would waste the whole run.
    with pytest.raises(ValueError) as excinfo:
        PyTubeProxy(config)

    message = str(excinfo.value)
    assert "YOUTUBE_PO_TOKEN" in message
    assert "YOUTUBE_VISITOR_DATA" in message


def test_configured_token_clears_the_pytubefix_token_cache(monkeypatch):
    # pytubefix caches the pair inside site-packages and prefers the cached
    # copy over the verifier, so a renewed token would stay shadowed by the
    # expired one it replaced.
    resets = []
    monkeypatch.setattr(pytube_proxy, "reset_cache", lambda: resets.append(True))

    PyTubeProxy(_token_config())

    assert resets == [True]


def test_without_a_token_the_pytubefix_cache_is_left_alone(monkeypatch):
    resets = []
    monkeypatch.setattr(pytube_proxy, "reset_cache", lambda: resets.append(True))

    PyTubeProxy(PyTubeYouTubeConfig())

    assert resets == []


def test_download_failure_with_a_token_points_at_the_doc(monkeypatch):
    _install_fake_youtube(monkeypatch, fail_clients=("WEB", "MWEB", "WEB_SAFARI"))

    proxy = PyTubeProxy(_token_config())
    with pytest.raises(RuntimeError) as excinfo:
        proxy._download_video_sync("abc123def45")

    message = str(excinfo.value)
    assert "docs/po-token.md" in message
    assert "expired" in message.lower()


def test_download_failure_without_a_token_says_nothing_about_it(monkeypatch):
    _install_fake_youtube(monkeypatch, fail_clients=("WEB", "MWEB", "WEB_SAFARI"))

    proxy = PyTubeProxy(PyTubeYouTubeConfig())
    with pytest.raises(RuntimeError) as excinfo:
        proxy._download_video_sync("abc123def45")

    assert "po-token" not in str(excinfo.value)


def test_rate_limit_still_aborts_when_a_token_is_configured(monkeypatch):
    # A 429 despite a configured token is how expiry shows up, but the abort
    # itself must not change: extra requests only prolong the block.
    attempts = []

    def factory(url, client=None, **kwargs):
        attempts.append(client)
        raise RateLimited()

    monkeypatch.setattr(pytube_proxy, "YouTube", factory)
    proxy = PyTubeProxy(_token_config())

    with pytest.raises(pytube_proxy.YouTubeRateLimitError) as excinfo:
        proxy._download_video_sync("abc123def45")

    assert attempts == ["WEB"]
    assert "docs/po-token.md" in str(excinfo.value)
