"""A 429 must stop the compilation, not send it through the whole pool.

`create_youtube_video_compilation` used to swallow every download failure and
continue. With ~150 ids across the configured channels that meant one throttled
run fired hundreds of requests at the endpoint already refusing it — which kept
the block alive well past when it would otherwise have expired, and surfaced to
the user only as "compilation completed with 0.0s duration".
"""

import pytest

from src.entities.configs.services.video import VideoConfig
from src.proxies.pytube_proxy import YouTubeRateLimitError
from src.services.video_service import VideoService


class _Proxy:
    """Lists a big pool; every download is throttled."""

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
