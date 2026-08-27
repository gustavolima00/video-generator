import logging
import os
import tempfile
from pathlib import Path
from typing import List, Literal

from src.entities.configs.proxies.youtube import BackgroundCacheConfig
from src.proxies.interfaces import IYouTubeProxy

logger = logging.getLogger(__name__)

# An mp4 always starts with a box whose type sits at offset 4; 'ftyp' is the
# first box every stream pytubefix hands us carries. Checking it costs one read
# and catches the failure that matters here — a file truncated by a crash mid
# download — without paying for a real demux on every hit.
_FTYP_OFFSET = 4
_FTYP = b"ftyp"
_MIN_MP4_SIZE = _FTYP_OFFSET + len(_FTYP)

_BYTES_PER_GIGABYTE = 1024 ** 3
# Only published entries count against the budget and are eligible for
# eviction: half-written temporaries carry a '.part' suffix, and anything else
# in the directory belongs to whoever put it there.
_ENTRY_GLOB = "*.mp4"


class CachingYouTubeProxy(IYouTubeProxy):
    """Serve background clips from disk, hitting YouTube only on a miss.

    The daily run reuses the same pool of clips, so re-downloading them is both
    the slowest step and the one that gets this IP throttled (HTTP 429). Keeping
    the mp4 bytes in a directory keyed by video id and quality track makes a
    repeated run independent of the network.

    Caching is deliberately best-effort: a cache that cannot be read or written
    degrades to a plain download instead of failing the run, because losing the
    day's video to a *cache* problem would be worse than having no cache at all.
    Everything else still fails fast — a download error (429 included) propagates
    untouched.
    """

    def __init__(self, inner: IYouTubeProxy, cache: BackgroundCacheConfig):
        self._inner = inner
        self._cache = cache
        self._dir = Path(os.path.expanduser(cache.dir))
        self._hits = 0
        self._misses = 0
        self._ensure_dir()

    async def list_video_ids(
        self,
        url: str,
        surface: Literal["videos", "shorts"] = "videos",
    ) -> List[str]:
        """List video IDs from a YouTube channel or playlist URL"""
        # Listings change as channels publish; only the clip bytes are cached.
        return await self._inner.list_video_ids(url, surface=surface)

    async def download_video(self, video_id: str, low_quality: bool = False) -> bytes:
        """Download a YouTube video and return its bytes"""
        path = self._entry_path(video_id, low_quality)

        cached = self._read_entry(path)
        if cached is not None:
            self._touch(path)
            self._hits += 1
            logger.info(
                "Background cache hit for %s (%s) — %d hits / %d misses this run",
                video_id, self._track(low_quality), self._hits, self._misses,
            )
            return cached

        self._misses += 1
        logger.info(
            "Background cache miss for %s (%s) — downloading; "
            "%d hits / %d misses this run",
            video_id, self._track(low_quality), self._hits, self._misses,
        )
        data = await self._inner.download_video(video_id, low_quality)
        self._store_entry(path, data)
        return data

    def locally_available(
        self, video_ids: List[str], low_quality: bool = False
    ) -> List[str]:
        """Which of *video_ids* are already on disk, in the order given.

        Only a cheap existence check: whether the bytes are actually usable is
        settled by `download_video`, which falls back to a download when an
        entry turns out to be broken. A caller that reached for this because
        the network is gone gets one skipped clip rather than a wrong answer.
        """
        return [
            video_id
            for video_id in video_ids
            if self._entry_path(video_id, low_quality).exists()
        ]

    # --- internals ------------------------------------------------------

    @staticmethod
    def _track(low_quality: bool) -> str:
        return "lq" if low_quality else "hq"

    def _entry_path(self, video_id: str, low_quality: bool) -> Path:
        # Quality tracks are separate entries: an lq clip must never be served
        # where an hq one was asked for.
        return self._dir / f"{video_id}-{self._track(low_quality)}.mp4"

    def _ensure_dir(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(
                "Background cache directory %s is unavailable (%s); "
                "this run will download every clip",
                self._dir, e,
            )

    def _read_entry(self, path: Path) -> bytes | None:
        """Return the cached bytes, or None when the entry is absent or unusable."""
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.warning("Could not read cached clip %s (%s); re-downloading", path, e)
            self._discard(path)
            return None

        if len(data) < _MIN_MP4_SIZE or data[_FTYP_OFFSET:_MIN_MP4_SIZE] != _FTYP:
            # A truncated or otherwise broken file would poison this video id
            # forever if it were kept, so drop it and fall through to a download.
            logger.warning(
                "Cached clip %s is not a usable mp4 (%d bytes); discarding it",
                path, len(data),
            )
            self._discard(path)
            return None

        return data

    def _store_entry(self, path: Path, data: bytes) -> None:
        """Publish *data* at *path* atomically; never raise on a cache problem."""
        tmp_path = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=self._dir, prefix=f".{path.name}.", suffix=".part"
            )
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(data)
            # Only os.replace makes the clip visible, so a reader never sees a
            # half-written file and concurrent writers just overwrite each other.
            os.replace(tmp_path, path)
        except OSError as e:
            logger.warning(
                "Could not cache %s (%s); the clip was still downloaded, "
                "so the run continues without it being cached",
                path.name, e,
            )
            if tmp_path is not None:
                self._discard(Path(tmp_path))
            return

        self._evict_until_within_budget()

    def _touch(self, path: Path) -> None:
        """Mark the entry as used, which is what the LRU order reads."""
        try:
            os.utime(path, None)
        except OSError as e:
            # An entry we cannot touch just looks older than it is; the clip was
            # still served, so this is not worth failing a run over.
            logger.warning(
                "Could not refresh the last-used time of %s (%s)", path, e
            )

    def _evict_until_within_budget(self) -> None:
        """Drop the least recently used entries until the cache fits its cap.

        Size and last-used time come straight from the filesystem, so there is no
        index to keep in sync: whatever is on disk is the truth, even after a run
        was killed mid-download or a second machine wrote to the same directory.
        """
        budget = self._cache.max_gigabytes * _BYTES_PER_GIGABYTE
        entries = []
        total = 0
        try:
            for entry in self._dir.glob(_ENTRY_GLOB):
                try:
                    stat = entry.stat()
                except FileNotFoundError:
                    continue  # a concurrent run evicted it; nothing to account for
                entries.append((stat.st_mtime, entry.name, entry, stat.st_size))
                total += stat.st_size
        except OSError as e:
            logger.warning(
                "Could not measure the background cache in %s (%s); "
                "leaving it as it is",
                self._dir, e,
            )
            return

        if total <= budget:
            return

        for _, _, entry, size in sorted(entries, key=lambda e: (e[0], e[1])):
            if total <= budget:
                break
            try:
                entry.unlink()
            except FileNotFoundError:
                pass  # already gone, and gone is what we wanted
            except OSError as e:
                logger.warning(
                    "Could not evict cached clip %s (%s); "
                    "the cache stays above its budget",
                    entry, e,
                )
                continue
            else:
                logger.info(
                    "Evicted %s (%d bytes) to keep the background cache "
                    "under %g GB",
                    entry.name, size, self._cache.max_gigabytes,
                )
            total -= size

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink()
        except OSError as e:
            logger.warning("Could not remove cache entry %s (%s)", path, e)
