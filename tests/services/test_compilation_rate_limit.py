"""A 429 must stop the compilation, not send it through the whole pool.

`create_youtube_video_compilation` used to swallow every download failure and
continue. With ~150 ids across the configured channels that meant one throttled
run fired hundreds of requests at the endpoint already refusing it — which kept
the block alive well past when it would otherwise have expired, and surfaced to
the user only as "compilation completed with 0.0s duration".
"""

from types import SimpleNamespace

import pytest

from src.entities.configs.services.video import VideoConfig
from src.proxies.interfaces import IYouTubeProxy
from src.proxies.pytube_proxy import YouTubeRateLimitError
from src.services import video_service
from src.services.video_service import VideoService


class _Proxy(IYouTubeProxy):
    """Lists a big pool; every download is throttled, and nothing is local."""

    def __init__(self, pool=150):
        self.pool = pool
        self.download_attempts = 0

    async def list_video_ids(self, url, surface="videos"):
        return [f"vid{i:08d}00" for i in range(self.pool)]

    async def download_video(self, video_id, low_quality=False):
        self.download_attempts += 1
        raise YouTubeRateLimitError("HTTP 429")


def _service(proxy):
    config = VideoConfig(
        watermark_path=None,
        call_to_action_path=None,
        youtube_channel_urls=["https://www.youtube.com/@a"],
        youtube_channel_strategy="all",
    )
    return VideoService(proxy, config)


@pytest.mark.asyncio
async def test_rate_limit_aborts_after_a_single_download_attempt():
    proxy = _Proxy()
    service = _service(proxy)

    with pytest.raises(YouTubeRateLimitError):
        await service.create_youtube_video_compilation(min_duration=60)

    assert proxy.download_attempts == 1, (
        f"walked {proxy.download_attempts} videos while throttled; "
        "should stop at the first 429"
    )


@pytest.mark.asyncio
async def test_rate_limit_surfaces_instead_of_the_0s_duration_message():
    # The old path reported an empty compilation, which hid the real cause.
    proxy = _Proxy()
    service = _service(proxy)

    with pytest.raises(YouTubeRateLimitError) as excinfo:
        await service.create_youtube_video_compilation(min_duration=60)

    assert "0.0s" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_ordinary_failures_still_skip_to_the_next_video():
    class Flaky(_Proxy):
        async def download_video(self, video_id, low_quality=False):
            self.download_attempts += 1
            raise RuntimeError("unavailable")

    proxy = Flaky(pool=5)
    service = _service(proxy)

    with pytest.raises(Exception) as excinfo:
        await service.create_youtube_video_compilation(min_duration=60)

    assert not isinstance(excinfo.value, YouTubeRateLimitError)
    assert proxy.download_attempts == 5, "should still try every candidate"


class _ProxyWithLocalClips(_Proxy):
    """Throttled by YouTube, but holding usable clips on disk.

    Mirrors the real shape of the problem: the network refuses us while the
    background cache already holds more than the compilation needs.
    """

    def __init__(self, local_ids, pool=150, seconds_each=40.0):
        super().__init__(pool=pool)
        self._local = list(local_ids)
        self.seconds_each = seconds_each
        self.served_locally = []

    def locally_available(self, video_ids, low_quality=False):
        return [v for v in video_ids if v in self._local]

    async def download_video(self, video_id, low_quality=False):
        self.download_attempts += 1
        if video_id in self._local:
            self.served_locally.append(video_id)
            return b"cached-clip-bytes"
        raise YouTubeRateLimitError("HTTP 429")


@pytest.fixture
def local_clips_play(monkeypatch):
    """Make cached bytes decode to a fixed-length clip."""

    class _Clip:
        def __init__(self, bytes=None, **kwargs):
            self.clip = SimpleNamespace(duration=40.0)

        def apply_anti_fingerprint(self, *_args, **_kwargs):
            pass

        def concat(self, _other):
            pass

    monkeypatch.setattr(video_service.video_clip, "VideoClip", _Clip)


@pytest.mark.asyncio
async def test_throttled_run_finishes_from_local_clips(local_clips_play):
    # The whole point of the cache: a throttled run still produces a video.
    pool = [f"vid{i:08d}00" for i in range(150)][:50]  # what the pool cap admits
    proxy = _ProxyWithLocalClips(local_ids=pool[-6:])
    service = _service(proxy)

    result = await service.create_youtube_video_compilation(min_duration=120)

    assert len(result.downloaded_bytes) == 3, "40s clips should cover 120s with 3"
    assert proxy.served_locally, "should have fallen back to the cached clips"


@pytest.mark.asyncio
async def test_fallback_makes_no_further_network_attempts(local_clips_play):
    pool = [f"vid{i:08d}00" for i in range(150)][:50]  # what the pool cap admits
    proxy = _ProxyWithLocalClips(local_ids=pool[-6:])
    service = _service(proxy)

    await service.create_youtube_video_compilation(min_duration=120)

    # One throttled attempt, then only local reads — never a walk of the pool.
    network_attempts = proxy.download_attempts - len(proxy.served_locally)
    assert network_attempts == 1, (
        f"made {network_attempts} network attempts while throttled; "
        "the fallback must not go back to YouTube"
    )


@pytest.mark.asyncio
async def test_throttle_still_raises_when_local_clips_cannot_cover_it(local_clips_play):
    pool = [f"vid{i:08d}00" for i in range(150)][:50]
    proxy = _ProxyWithLocalClips(local_ids=pool[-1:])  # 40s only
    service = _service(proxy)

    with pytest.raises(YouTubeRateLimitError):
        await service.create_youtube_video_compilation(min_duration=300)


@pytest.mark.asyncio
async def test_throttle_raises_the_original_error_not_a_bare_reraise(local_clips_play):
    proxy = _ProxyWithLocalClips(local_ids=[])
    service = _service(proxy)

    with pytest.raises(YouTubeRateLimitError) as excinfo:
        await service.create_youtube_video_compilation(min_duration=60)

    assert "HTTP 429" in str(excinfo.value)
