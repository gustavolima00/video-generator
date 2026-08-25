import asyncio
import logging
import re
import tempfile
from typing import Any, List, Literal

from pytubefix import YouTube, Channel, Playlist
from pytubefix.helpers import reset_cache

from src.proxies.interfaces import IYouTubeProxy
from src.entities.configs.proxies.youtube import PyTubeYouTubeConfig

logger = logging.getLogger(__name__)


class YouTubeRateLimitError(RuntimeError):
    """YouTube answered HTTP 429 — this IP is being throttled.

    Separate from a per-video failure because the remedy is different: the
    throttle applies to the whole address, so neither another client nor
    another video can succeed. Callers must stop rather than retry, since
    every extra request extends the block.
    """


def _is_rate_limited(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 429 or "429" in str(exc)


_PO_TOKEN_DOC = "docs/po-token.md"


class PyTubeProxy(IYouTubeProxy):
    def __init__(self, config: PyTubeYouTubeConfig):
        self.config = config
        self._po_token_kwargs = self._build_po_token_kwargs(config)
        if self._po_token_kwargs:
            # pytubefix keeps the pair in a cache file inside its own package
            # and reads it in preference to the verifier, so a token renewed in
            # the .env would stay shadowed by the expired one it replaced.
            reset_cache()

    @staticmethod
    def _build_po_token_kwargs(config: PyTubeYouTubeConfig) -> dict:
        """Build the pytubefix arguments that install the operator's po_token.

        Left to itself pytubefix mints a poToken through botGuard, and that
        synthetic token is what YouTube is answering with 429 — it carries no
        real browser session. A token captured from one takes precedence over
        it. Without a token configured the arguments are empty, so downloads
        are built exactly as they were before this existed.
        """
        po_token, visitor_data = config.po_token, config.visitor_data
        if not po_token and not visitor_data:
            return {}
        if not (po_token and visitor_data):
            raise ValueError(
                "YOUTUBE_PO_TOKEN and YOUTUBE_VISITOR_DATA go together: YouTube "
                "only accepts a po_token alongside the visitor id it was issued "
                f"for. Set both or neither — see {_PO_TOKEN_DOC}."
            )
        return {
            "use_po_token": True,
            # pytubefix unpacks the verifier as (visitorData, po_token).
            "po_token_verifier": lambda: (visitor_data, po_token),
        }

    def _token_hint(self) -> str:
        """Suffix pointing at the doc when a configured token may have expired.

        Tokens are captured by hand and go stale silently: YouTube refuses an
        expired one exactly as it refuses none, so the failure looks identical
        to the problem the token was installed to fix.
        """
        if not self._po_token_kwargs:
            return ""
        return (
            " A po_token is configured, so it may have expired — YouTube refuses "
            f"an expired token the same way it refuses none; see {_PO_TOKEN_DOC} "
            "to renew it."
        )

    async def list_video_ids(
        self,
        url: str,
        surface: Literal["videos", "shorts"] = "videos",
    ) -> List[str]:
        """List video IDs from a YouTube channel or playlist URL"""
        return await asyncio.to_thread(self._extract_video_ids, url, surface)

    def _extract_video_ids(
        self,
        url: str,
        surface: Literal["videos", "shorts"] = "videos",
    ) -> List[str]:
        try:
            if "playlist" in url or "list=" in url:
                playlist = Playlist(url)
                video_ids = [vid for vid in playlist.video_urls]
            elif (
                "channel/" in url
                or "c/" in url
                or "user/" in url
                or url.startswith("https://www.youtube.com/@")
            ):
                channel = Channel(url)
                if surface == "shorts" or url.rstrip("/").endswith("/shorts"):
                    video_ids = self._collect_video_ids(channel.shorts)
                    if not video_ids:
                        channel.html_url = channel.shorts_url
                        video_ids = self._collect_video_ids(channel.initial_data)
                    return video_ids

                video_ids = self._collect_video_ids(channel.video_urls)
                if not video_ids:
                    channel.html_url = channel.videos_url
                    video_ids = self._collect_video_ids(channel.initial_data)
                return video_ids
            else:
                # Assume it's a single video url
                yt = YouTube(url)
                video_ids = [yt.watch_url]
        except Exception as e:
            logger.error(f"Failed to list video IDs for {url}: {e}")
            raise e

        return self._collect_video_ids(video_ids)

    @classmethod
    def _collect_video_ids(cls, value: Any) -> List[str]:
        """Extract unique YouTube video IDs from pytubefix channel shapes.

        pytubefix returns ``Channel.video_urls`` entries as empty lists for
        some handle URLs, while ``initial_data`` still has ``videoId`` fields.
        This recursive collector handles both the old URL list shape and the
        current nested dict/list shape.
        """
        extracted: list[str] = []
        seen: set[str] = set()

        def add(video_id: str | None) -> None:
            if (
                video_id
                and re.fullmatch(r"[-_A-Za-z0-9]{11}", video_id)
                and video_id not in seen
            ):
                seen.add(video_id)
                extracted.append(video_id)

        def visit(item: Any) -> None:
            if hasattr(item, "video_id"):
                add(item.video_id)
                return

            if isinstance(item, str):
                if "v=" in item:
                    add(item.split("v=")[1].split("&")[0])
                return

            if isinstance(item, dict):
                video_id = item.get("videoId")
                if isinstance(video_id, str):
                    add(video_id)
                for nested in item.values():
                    visit(nested)
                return

            if isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)

        visit(value)
        return extracted

    async def download_video(self, video_id: str, low_quality: bool = False) -> bytes:
        """Download a YouTube video and return its bytes"""
        return await asyncio.to_thread(self._download_video_sync, video_id, low_quality)

    def _download_video_sync(self, video_id: str, low_quality: bool = False) -> bytes:
        """Download *video_id*, trying each configured client in turn.

        The client has to be named explicitly: pytubefix defaults to
        ANDROID_VR, which YouTube now answers with a bot-detection error, so
        leaving it implicit fails every download. Clients get blocked one at a
        time rather than all at once, hence the fallback list.
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        failures: list[str] = []

        for client in self.config.download_clients:
            try:
                return self._download_with_client(url, video_id, client, low_quality)
            except Exception as e:
                if _is_rate_limited(e):
                    # Throttling is per-IP, so the remaining clients would only
                    # add requests to a limit we have already exceeded.
                    logger.error(
                        "YouTube is rate-limiting this IP (429) on client %s; "
                        "giving up on %s without trying the rest",
                        client, video_id,
                    )
                    raise YouTubeRateLimitError(
                        f"YouTube returned HTTP 429 for {video_id} (client {client}). "
                        "This IP is throttled — further requests prolong it."
                        f"{self._token_hint()}"
                    ) from e
                failures.append(f"{client}: {type(e).__name__}: {e}")
                logger.warning(
                    "Client %s failed for %s (%s: %s), trying the next one",
                    client, video_id, type(e).__name__, e,
                )

        detail = " | ".join(failures) or "no clients configured"
        logger.error("Failed to download video %s: %s", video_id, detail)
        raise RuntimeError(
            f"Failed to download video {video_id}: {detail}{self._token_hint()}"
        )

    def _download_with_client(
        self, url: str, video_id: str, client: str, low_quality: bool
    ) -> bytes:
        yt = YouTube(url, client=client, **self._po_token_kwargs)

        if not low_quality:
            result = self._try_adaptive_download(yt)
            if result is not None:
                return result

        streams = yt.streams.filter(
            progressive=True, file_extension="mp4"
        ).order_by("resolution")
        stream = streams.first() if low_quality else streams.desc().first()
        if not stream:
            fallback_streams = yt.streams.filter(file_extension="mp4").order_by(
                "resolution"
            )
            stream = (
                fallback_streams.first()
                if low_quality
                else fallback_streams.desc().first()
            )
            if not stream:
                raise ValueError(
                    f"No suitable mp4 stream found for video_id {video_id}"
                )

        logger.info(
            "Downloading progressive %s for %s via %s",
            stream.resolution, video_id, client,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = stream.download(output_path=temp_dir)
            with open(file_path, "rb") as f:
                return f.read()

    def _try_adaptive_download(self, yt: YouTube) -> bytes | None:
        """Download a video-only adaptive stream (no audio needed).

        Returns the MP4 bytes, or None if adaptive streams are
        unavailable so the caller can fall back to progressive.
        """
        candidates = (
            yt.streams
            .filter(adaptive=True, file_extension="mp4", only_video=True, res="1080p")
        )
        if not candidates:
            candidates = (
                yt.streams
                .filter(adaptive=True, file_extension="mp4", only_video=True, res="720p")
            )
        video_stream = candidates.first() if candidates else None
        if not video_stream:
            return None

        logger.info(
            "Downloading adaptive %s video-only for %s",
            video_stream.resolution, yt.video_id,
        )

        with tempfile.TemporaryDirectory() as td:
            file_path = video_stream.download(output_path=td, filename="video.mp4")
            with open(file_path, "rb") as f:
                return f.read()
