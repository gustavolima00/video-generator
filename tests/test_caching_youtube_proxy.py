import asyncio
import logging
import os
from pathlib import Path

import pytest

from src.entities.configs.proxies.youtube import (
    BackgroundCacheConfig,
    PyTubeYouTubeConfig,
)
from src.proxies.caching_youtube_proxy import CachingYouTubeProxy
from src.proxies.factories import YouTubeProxyFactory
from src.proxies.interfaces import IYouTubeProxy
from src.proxies.pytube_proxy import PyTubeProxy, YouTubeRateLimitError


def mp4_bytes(payload: bytes = b"background-clip") -> bytes:
    """Minimal bytes that pass the cheap mp4 check: an `ftyp` box at offset 4."""
    return b"\x00\x00\x00\x18ftypmp42" + payload


class FakeYouTubeProxy(IYouTubeProxy):
    """Inner proxy that records every call and never touches the network."""

    def __init__(self, payload: bytes = mp4_bytes(), error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.download_calls: list[tuple[str, bool]] = []
        self.list_calls: list[tuple[str, str]] = []

    async def list_video_ids(self, url: str, surface: str = "videos"):
        self.list_calls.append((url, surface))
        return ["abc123def45", "xyz987uvw65"]

    async def download_video(self, video_id: str, low_quality: bool = False) -> bytes:
        self.download_calls.append((video_id, low_quality))
        if self.error is not None:
            raise self.error
        if callable(self.payload):
            return self.payload(video_id, low_quality)
        return self.payload


def cache_config(tmp_path: Path, **overrides) -> BackgroundCacheConfig:
    return BackgroundCacheConfig(dir=str(tmp_path), **overrides)


def build(tmp_path: Path, inner: FakeYouTubeProxy, **overrides) -> CachingYouTubeProxy:
    return CachingYouTubeProxy(inner=inner, cache=cache_config(tmp_path, **overrides))


# --- C1: listing is not the cache's business ---------------------------------


def test_list_video_ids_delegates_without_touching_the_cache(tmp_path):
    inner = FakeYouTubeProxy()
    proxy = build(tmp_path, inner)

    result = asyncio.run(proxy.list_video_ids("https://youtube.com/@chan", surface="shorts"))

    assert result == ["abc123def45", "xyz987uvw65"]
    assert inner.list_calls == [("https://youtube.com/@chan", "shorts")]
    assert list(tmp_path.iterdir()) == []


# --- C2/C3: miss downloads and persists, hit serves from disk ----------------


def test_miss_calls_inner_once_and_persists_the_clip(tmp_path):
    inner = FakeYouTubeProxy()
    proxy = build(tmp_path, inner)

    assert asyncio.run(proxy.download_video("abc123def45")) == mp4_bytes()

    assert inner.download_calls == [("abc123def45", False)]
    entry = tmp_path / "abc123def45-hq.mp4"
    assert entry.read_bytes() == mp4_bytes()


def test_second_download_is_served_from_disk_without_calling_inner(tmp_path):
    inner = FakeYouTubeProxy()
    proxy = build(tmp_path, inner)

    first = asyncio.run(proxy.download_video("abc123def45"))
    second = asyncio.run(proxy.download_video("abc123def45"))

    assert first == second == mp4_bytes()
    assert inner.download_calls == [("abc123def45", False)], "hit must not hit the network"


def test_hit_is_served_by_a_fresh_proxy_instance(tmp_path):
    # The filesystem is the source of truth: a new run (new instance) still hits.
    asyncio.run(build(tmp_path, FakeYouTubeProxy()).download_video("abc123def45"))

    inner = FakeYouTubeProxy()
    assert asyncio.run(build(tmp_path, inner).download_video("abc123def45")) == mp4_bytes()
    assert inner.download_calls == []


def test_hit_survives_a_failing_inner_proxy(tmp_path):
    # SC-002: with a warm cache the run completes even while YouTube throttles us.
    asyncio.run(build(tmp_path, FakeYouTubeProxy()).download_video("abc123def45"))

    throttled = FakeYouTubeProxy(error=YouTubeRateLimitError("429"))
    proxy = build(tmp_path, throttled)

    assert asyncio.run(proxy.download_video("abc123def45")) == mp4_bytes()
    assert throttled.download_calls == []


# --- C4: an unusable entry never poisons the id ------------------------------


@pytest.mark.parametrize(
    "corrupted",
    [b"", b"tiny", b"not-an-mp4-file-at-all", mp4_bytes()[:6]],
    ids=["empty", "tiny", "no-ftyp-box", "truncated-header"],
)
def test_corrupted_entry_is_discarded_and_redownloaded(tmp_path, corrupted):
    entry = tmp_path / "abc123def45-hq.mp4"
    entry.write_bytes(corrupted)
    inner = FakeYouTubeProxy()
    proxy = build(tmp_path, inner)

    assert asyncio.run(proxy.download_video("abc123def45")) == mp4_bytes()

    assert inner.download_calls == [("abc123def45", False)]
    assert entry.read_bytes() == mp4_bytes(), "the bad entry must be replaced, not kept"


def test_unreadable_entry_falls_back_to_a_download(tmp_path):
    entry = tmp_path / "abc123def45-hq.mp4"
    entry.write_bytes(mp4_bytes())
    entry.chmod(0o000)
    inner = FakeYouTubeProxy()
    proxy = build(tmp_path, inner)

    try:
        assert asyncio.run(proxy.download_video("abc123def45")) == mp4_bytes()
    finally:
        if entry.exists():
            entry.chmod(0o600)

    assert inner.download_calls == [("abc123def45", False)]


# --- C5: failures propagate untouched ----------------------------------------


@pytest.mark.parametrize(
    "error",
    [YouTubeRateLimitError("this IP is throttled"), RuntimeError("video unavailable")],
    ids=["rate-limit", "generic"],
)
def test_inner_failure_propagates_and_writes_nothing(tmp_path, error):
    inner = FakeYouTubeProxy(error=error)
    proxy = build(tmp_path, inner)

    with pytest.raises(type(error)) as excinfo:
        asyncio.run(proxy.download_video("abc123def45"))

    assert excinfo.value is error
    assert list(tmp_path.iterdir()) == []


# --- C6: caching is best-effort ----------------------------------------------


def test_unwritable_directory_degrades_to_download_only(tmp_path, caplog):
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    read_only.chmod(0o500)
    inner = FakeYouTubeProxy()

    try:
        proxy = CachingYouTubeProxy(inner=inner, cache=cache_config(read_only))
        with caplog.at_level(logging.WARNING):
            assert asyncio.run(proxy.download_video("abc123def45")) == mp4_bytes()
    finally:
        read_only.chmod(0o700)

    assert inner.download_calls == [("abc123def45", False)]
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert list(read_only.iterdir()) == []


def test_missing_directory_is_created_and_the_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    inner = FakeYouTubeProxy()

    proxy = CachingYouTubeProxy(
        inner=inner, cache=BackgroundCacheConfig(dir="~/cache/backgrounds")
    )
    asyncio.run(proxy.download_video("abc123def45"))

    assert (tmp_path / "cache/backgrounds/abc123def45-hq.mp4").read_bytes() == mp4_bytes()


# --- C3/C9: writes are atomic ------------------------------------------------


def test_no_partial_file_is_visible_while_the_download_runs(tmp_path):
    entry = tmp_path / "abc123def45-hq.mp4"
    seen: list[bool] = []

    class SlowProxy(FakeYouTubeProxy):
        async def download_video(self, video_id, low_quality=False):
            seen.append(entry.exists())
            await asyncio.sleep(0)
            return mp4_bytes()

    proxy = build(tmp_path, SlowProxy())
    asyncio.run(proxy.download_video("abc123def45"))

    assert seen == [False]
    assert entry.read_bytes() == mp4_bytes()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != entry.name]
    assert leftovers == [], f"temporary files must not survive the write: {leftovers}"


def test_concurrent_misses_both_return_valid_bytes(tmp_path):
    class PerCallProxy(FakeYouTubeProxy):
        def __init__(self):
            super().__init__()
            self.served = 0

        async def download_video(self, video_id, low_quality=False):
            self.served += 1
            payload = mp4_bytes(f"clip-{self.served}".encode())
            await asyncio.sleep(0)
            return payload

    inner = PerCallProxy()
    proxy = build(tmp_path, inner)

    async def race():
        return await asyncio.gather(
            proxy.download_video("abc123def45"),
            proxy.download_video("abc123def45"),
        )

    results = asyncio.run(race())

    assert all(r.startswith(b"\x00\x00\x00\x18ftyp") for r in results)
    stored = (tmp_path / "abc123def45-hq.mp4").read_bytes()
    assert stored in results, "the stored entry must be one complete clip, never a mix"


# --- C7: quality tracks are independent --------------------------------------


def test_quality_tracks_never_serve_each_other(tmp_path):
    def by_quality(video_id, low_quality):
        return mp4_bytes(b"lq" if low_quality else b"hq")

    inner = FakeYouTubeProxy(payload=by_quality)
    proxy = build(tmp_path, inner)

    assert asyncio.run(proxy.download_video("abc123def45")) == mp4_bytes(b"hq")
    assert asyncio.run(proxy.download_video("abc123def45", low_quality=True)) == mp4_bytes(b"lq")
    assert asyncio.run(proxy.download_video("abc123def45")) == mp4_bytes(b"hq")
    assert asyncio.run(proxy.download_video("abc123def45", low_quality=True)) == mp4_bytes(b"lq")

    assert inner.download_calls == [("abc123def45", False), ("abc123def45", True)]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "abc123def45-hq.mp4",
        "abc123def45-lq.mp4",
    ]


# --- C10: hits and misses are observable -------------------------------------


def test_hits_and_misses_are_logged_with_running_counters(tmp_path, caplog):
    proxy = build(tmp_path, FakeYouTubeProxy())

    with caplog.at_level(logging.INFO, logger="src.proxies.caching_youtube_proxy"):
        asyncio.run(proxy.download_video("abc123def45"))
        asyncio.run(proxy.download_video("abc123def45"))

    messages = [record.getMessage() for record in caplog.records]
    assert any("miss" in m.lower() and "abc123def45" in m for m in messages)
    assert any("hit" in m.lower() and "abc123def45" in m for m in messages)
    counters = " ".join(messages)
    assert "1" in counters, f"hit/miss counters should be reported: {messages}"


# --- C11: the decorator is opt-out at the factory ----------------------------


def test_factory_wraps_the_proxy_when_the_cache_is_enabled(tmp_path):
    config = PyTubeYouTubeConfig(cache=cache_config(tmp_path))

    proxy = YouTubeProxyFactory.create(config)

    assert isinstance(proxy, CachingYouTubeProxy)
    assert isinstance(proxy._inner, PyTubeProxy)


def test_factory_returns_the_bare_proxy_when_the_cache_is_disabled(tmp_path):
    config = PyTubeYouTubeConfig(cache=cache_config(tmp_path, enabled=False))

    proxy = YouTubeProxyFactory.create(config)

    assert isinstance(proxy, PyTubeProxy)
    assert not isinstance(proxy, CachingYouTubeProxy)


def test_disabled_cache_does_not_create_its_directory(tmp_path):
    target = tmp_path / "never-created"
    YouTubeProxyFactory.create(
        PyTubeYouTubeConfig(cache=BackgroundCacheConfig(dir=str(target), enabled=False))
    )

    assert not target.exists()


# --- T003: config regression --------------------------------------------------


def test_config_without_a_cache_block_keeps_working_with_defaults():
    config = PyTubeYouTubeConfig(**{"type": "pytube", "download_clients": ["WEB"]})

    assert config.cache.enabled is True
    assert config.cache.dir == "~/.cache/video-generator/backgrounds"
    assert config.cache.max_gigabytes == 20.0


def test_cache_block_from_yaml_overrides_the_defaults():
    config = PyTubeYouTubeConfig(
        **{"type": "pytube", "cache": {"enabled": False, "dir": "/data/bg", "max_gigabytes": 5}}
    )

    assert config.cache.enabled is False
    assert config.cache.dir == "/data/bg"
    assert config.cache.max_gigabytes == 5.0


def test_cache_defaults_are_not_shared_between_configs():
    first = PyTubeYouTubeConfig()
    second = PyTubeYouTubeConfig()

    first.cache.dir = "/tmp/first"

    assert second.cache.dir == "~/.cache/video-generator/backgrounds"


def test_cache_cap_must_be_positive():
    with pytest.raises(ValueError):
        BackgroundCacheConfig(max_gigabytes=0)
