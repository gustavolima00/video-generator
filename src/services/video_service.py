import io
import logging
import random
from dataclasses import dataclass
from typing import Optional, List

import numpy as np
from moviepy import CompositeVideoClip, VideoClip as MoviepyVideoClip
from moviepy.video.fx import CrossFadeIn
from PIL import Image, ImageFilter

from ..proxies.interfaces import IYouTubeProxy
from ..proxies.pytube_proxy import YouTubeRateLimitError
from ..entities.configs.services.video import VideoConfig
from ..entities.image_story import ImageStory

from ..entities.editor import image_clip, audio_clip, video_clip, captions_clip


logger = logging.getLogger(__name__)


@dataclass
class YouTubeCompilationResult:
    clip: video_clip.VideoClip
    downloaded_bytes: List[bytes]


class VideoService:
    """Video generation service implementation"""

    def __init__(
        self,
        youtube_proxy: IYouTubeProxy,
        video_config: VideoConfig,
    ):
        self._youtube_proxy = youtube_proxy
        self._video_config = video_config
        self._watermark_bytes = None
        if self._video_config.watermark_path:
            with open(self._video_config.watermark_path, "rb") as f:
                self._watermark_bytes = f.read()

        self._call_to_action_bytes = None
        if self._video_config.call_to_action_path:
            with open(self._video_config.call_to_action_path, "rb") as f:
                self._call_to_action_bytes = f.read()

    async def create_youtube_video_compilation(
        self, min_duration: int, low_quality: bool = False
    ) -> YouTubeCompilationResult:
        """Create video compilation from YouTube content"""

        video_ids = await self._list_youtube_compilation_video_ids()
        random.shuffle(video_ids)

        video = video_clip.VideoClip()
        downloaded_bytes: List[bytes] = []
        total_duration = 0

        for video_id in video_ids:
            try:
                video_bytes = await self._youtube_proxy.download_video(
                    video_id, low_quality
                )
                new_video = video_clip.VideoClip(bytes=video_bytes)
                duration = float(new_video.clip.duration or 0)
            except YouTubeRateLimitError:
                # Every remaining video would hit the same per-IP throttle, and
                # the pool is ~150 ids across the configured channels — walking
                # it here is what kept the block alive between runs.
                logger.error(
                    "Aborting compilation after %d clip(s): YouTube is "
                    "rate-limiting this IP",
                    len(downloaded_bytes),
                )
                raise
            except Exception:
                logger.exception("Skipping unusable YouTube background %s", video_id)
                continue

            if duration <= 0:
                logger.warning(
                    "Skipping zero-duration YouTube background %s",
                    video_id,
                )
                continue

            downloaded_bytes.append(video_bytes)
            new_video.apply_anti_fingerprint(self._video_config.anti_fingerprint)
            video.concat(new_video)
            total_duration += duration

            if total_duration >= min_duration:
                return YouTubeCompilationResult(
                    clip=video, downloaded_bytes=downloaded_bytes
                )
        if total_duration < min_duration:
            raise Exception(
                f"Video compilation completed with {total_duration:.1f}s duration (all available videos used)"
            )
        return YouTubeCompilationResult(clip=video, downloaded_bytes=downloaded_bytes)

    async def _list_youtube_compilation_video_ids(self) -> List[str]:
        channel_urls = self._youtube_channel_urls()
        strategy = self._video_config.youtube_channel_strategy

        if strategy == "random":
            channel_url = random.choice(channel_urls)
            logger.info("Creating YouTube compilation from channel %s", channel_url)
            return await self._list_channel_video_ids(channel_url)

        if strategy == "all":
            logger.info(
                "Creating YouTube compilation from %d configured channels",
                len(channel_urls),
            )
            video_ids: List[str] = []
            seen: set[str] = set()
            failures: list[str] = []

            for channel_url in channel_urls:
                try:
                    channel_video_ids = await self._list_channel_video_ids(channel_url)
                except Exception as exc:
                    logger.exception(
                        "Skipping YouTube channel %s after list failure", channel_url
                    )
                    failures.append(f"{channel_url}: {exc}")
                    continue

                for video_id in channel_video_ids:
                    if video_id not in seen:
                        seen.add(video_id)
                        video_ids.append(video_id)

            if not video_ids and failures:
                raise RuntimeError(
                    "Failed to list usable YouTube backgrounds from any configured "
                    f"channel. First failure: {failures[0]}"
                )
            return video_ids

        raise ValueError(f"Unknown YouTube channel strategy: {strategy}")

    async def _list_channel_video_ids(self, channel_url: str) -> List[str]:
        video_ids = await self._youtube_proxy.list_video_ids(
            channel_url,
            surface=self._video_config.youtube_surface,
        )

        pool = self._video_config.youtube_pool_size
        if pool > 0:
            return video_ids[:pool]
        return video_ids

    def _youtube_channel_urls(self) -> List[str]:
        channel_urls = [
            url.strip()
            for url in self._video_config.youtube_channel_urls
            if url and url.strip()
        ]
        if not channel_urls and self._video_config.youtube_channel_url:
            channel_urls = [self._video_config.youtube_channel_url.strip()]
        if not channel_urls:
            raise ValueError("At least one YouTube channel URL must be configured")
        return channel_urls

    def generate_video(
        self,
        audio: audio_clip.AudioClip,
        background_video: video_clip.VideoClip,
        low_quality: bool = False,
        cover: Optional[image_clip.ImageClip] = None,
        captions: Optional[captions_clip.CaptionsClip] = None,
        cta_start: float = 0,
    ) -> video_clip.VideoClip:
        """Generate final video with all components.

        The narration runs from the first frame; the cover sits on top of it
        for ``cover_duration`` seconds. The cover is deliberately narrower
        than the frame and the captions sit below it, so the opening subtitles
        stay readable while it is up. When *cta_start* is positive the
        configured CTA image is composited at that time.
        """
        config = self._video_config
        size_rate = 1.0
        if low_quality:
            size_rate = 400 / config.height
            config = config.model_copy(
                update=dict(
                    width=int(round(config.width * size_rate)),
                    height=int(round(config.height * size_rate)),
                    padding=int(round(config.padding * size_rate)),
                )
            )

        audio.add_end_silence(config.end_silece_seconds)
        background_video.resize(config.width, config.height)
        background_video.ajust_duration(audio.clip.duration)
        background_video.set_audio(audio)

        width, height = background_video.clip.size
        total_duration = audio.clip.duration
        fade = self.CROSSFADE_DURATION

        # --- cover ---
        if cover is not None:
            cover.fit_width(int(width * config.cover_width_ratio))
            cover.center(width, height)
            cover_dur = float(config.cover_duration)
            cover.set_duration(cover_dur)
            cover.apply_fadeout(min(0.3, cover_dur))
            background_video.merge(cover)

        # --- CTA image overlay ---
        if self._call_to_action_bytes is not None and cta_start > 0 and cta_start < total_duration:
            cta_clip = image_clip.ImageClip(bytes=self._call_to_action_bytes)
            cta_clip.fit_width(width, config.padding)
            cta_clip.center(width, height)
            cta_clip.set_start(cta_start)
            cta_clip.set_duration(total_duration - cta_start)
            cta_clip.apply_fadein(fade)
            background_video.merge(cta_clip)

        # --- watermark ---
        if self._watermark_bytes is not None:
            water_mark = image_clip.ImageClip(bytes=self._watermark_bytes)
            water_mark.fit_width(width, config.padding)
            water_mark.center(width, height)
            water_mark.set_duration(total_duration)
            background_video.merge(water_mark)

        # --- captions ---
        if captions is not None:
            background_video.insert_captions(captions, size_rate=size_rate)
        return background_video

    CROSSFADE_DURATION = 0.5
    KEN_BURNS_MAX_SCALE = 1.12

    def generate_image_story_video(
        self,
        audio: audio_clip.AudioClip,
        image_story: ImageStory,
        generated_images: List[bytes],
        cover: Optional[image_clip.ImageClip] = None,
        captions: Optional[captions_clip.CaptionsClip] = None,
        low_quality: bool = False,
    ) -> video_clip.VideoClip:
        """Generate a video from a timed sequence of AI-generated images.

        The video has three phases:
        1. Introduction: the cover holds the screen for ``cover_duration``
           seconds over a blurred first image. The narration waits — it only
           starts once the cover fades out and the image unblurs.
        2. Story: images appear at their scheduled times filling the background.
           Ken Burns zoom + crossfade transitions between images.
        3. Call-to-action: the active image blurs and a CTA overlay appears.
        """
        config = self._video_config
        size_rate = 1.0
        if low_quality:
            size_rate = 400 / config.height
            config = config.model_copy(
                update=dict(
                    width=int(round(config.width * size_rate)),
                    height=int(round(config.height * size_rate)),
                    padding=int(round(config.padding * size_rate)),
                )
            )

        width, height = config.width, config.height
        audio.add_end_silence(config.end_silece_seconds)
        total_duration = audio.clip.duration

        # The cover overlays the opening of the story rather than delaying it.
        intro_end = float(config.cover_duration)
        cta_start = image_story.call_to_action_start_time

        segments = self._build_image_segments(
            image_story, generated_images, total_duration, intro_end, cta_start
        )

        fade = self.CROSSFADE_DURATION
        draw_dur = config.draw_transition_duration
        overlap = max(fade, draw_dur) if draw_dur > 0 else fade
        moviepy_clips = []
        first_visible = True
        for idx, (start, end, img_bytes, is_blurred) in enumerate(segments):
            extended_end = (
                min(end + overlap, total_duration) if idx < len(segments) - 1 else end
            )
            clip_duration = extended_end - start

            if is_blurred:
                img_bytes = self._blur_image_bytes(img_bytes)
                clip = image_clip.ImageClip(bytes=img_bytes)
                clip.clip = clip.clip.resized(new_size=(width, height))
                clip.set_start(start)
                clip.set_duration(clip_duration)
                moviepy_clips.append(clip.clip)
            else:
                zoom_in = random.choice([True, False])
                use_draw = draw_dur > 0 and not first_visible
                kb_clip = self._create_ken_burns_clip(
                    img_bytes, width, height, clip_duration, zoom_in,
                )
                kb_clip = kb_clip.with_start(start)
                if use_draw:
                    mask = self._create_brush_mask_clip(
                        width, height, clip_duration, draw_dur,
                    )
                    kb_clip = kb_clip.with_mask(mask)
                elif not first_visible:
                    kb_clip = CrossFadeIn(duration=fade).apply(kb_clip)
                first_visible = False
                moviepy_clips.append(kb_clip)

        if cover is not None and intro_end > 0:
            cover.fit_width(int(width * config.cover_width_ratio))
            cover.center(width, height)
            cover.set_start(0)
            cover.set_duration(intro_end)
            cover.apply_fadeout(min(0.3, intro_end))
            moviepy_clips.append(cover.clip)

        if self._call_to_action_bytes is not None and cta_start < total_duration:
            cta_clip = image_clip.ImageClip(bytes=self._call_to_action_bytes)
            cta_clip.fit_width(width, config.padding)
            cta_clip.center(width, height)
            cta_clip.set_start(cta_start)
            cta_clip.set_duration(total_duration - cta_start)
            cta_clip.apply_fadein(fade)
            moviepy_clips.append(cta_clip.clip)

        result = video_clip.VideoClip()
        result.clip = CompositeVideoClip(moviepy_clips, size=(width, height))
        result.clip = result.clip.with_duration(total_duration)
        result.clip = result.clip.with_audio(audio.clip)

        if self._watermark_bytes is not None:
            water_mark = image_clip.ImageClip(bytes=self._watermark_bytes)
            water_mark.fit_width(width, config.padding)
            water_mark.center(width, height)
            water_mark.set_duration(total_duration)
            result.merge(water_mark)

        if captions is not None:
            result.insert_captions(captions, size_rate=size_rate)

        return result

    @staticmethod
    def _build_image_segments(
        image_story: ImageStory,
        generated_images: List[bytes],
        total_duration: float,
        intro_end: float,
        cta_start: float,
    ) -> List[tuple]:
        """Return (start, end, image_bytes, is_blurred) segments."""
        segments: List[tuple] = []
        images = image_story.images

        for i, (story_img, img_bytes) in enumerate(zip(images, generated_images)):
            img_start = story_img.start_time
            img_end = (
                images[i + 1].start_time if i + 1 < len(images) else total_duration
            )
            if img_end <= img_start:
                continue

            if i == 0 and intro_end > img_start:
                blur_end = min(intro_end, img_end)
                segments.append((img_start, blur_end, img_bytes, True))
                if intro_end < img_end:
                    img_start = intro_end
                else:
                    continue

            if img_start < cta_start < img_end:
                segments.append((img_start, cta_start, img_bytes, False))
                segments.append((cta_start, img_end, img_bytes, True))
            elif img_start >= cta_start:
                segments.append((img_start, img_end, img_bytes, True))
            else:
                segments.append((img_start, img_end, img_bytes, False))

        return segments

    BRUSH_FEATHER = 0.06
    BRUSH_STROKE_COUNT = 4

    @classmethod
    def _create_ken_burns_clip(
        cls,
        img_bytes: bytes,
        width: int,
        height: int,
        duration: float,
        zoom_in: bool,
    ) -> MoviepyVideoClip:
        max_s = cls.KEN_BURNS_MAX_SCALE
        img = Image.open(io.BytesIO(img_bytes)).resize(
            (int(width * max_s), int(height * max_s)), Image.LANCZOS
        )
        img_array = np.array(img)
        base_h, base_w = img_array.shape[:2]

        def make_frame(t):
            progress = t / max(duration, 0.001)
            if zoom_in:
                scale = max_s - (max_s - 1.0) * progress
            else:
                scale = 1.0 + (max_s - 1.0) * progress
            crop_w = int(width * scale)
            crop_h = int(height * scale)
            x = (base_w - crop_w) // 2
            y = (base_h - crop_h) // 2
            cropped = img_array[y : y + crop_h, x : x + crop_w]
            return np.array(
                Image.fromarray(cropped).resize((width, height), Image.LANCZOS)
            )

        return MoviepyVideoClip(make_frame, duration=duration)

    @classmethod
    def _create_brush_mask_clip(
        cls, width: int, height: int, duration: float, draw_duration: float,
    ) -> MoviepyVideoClip:
        """Create a grayscale mask clip that reveals via the brush pattern.

        Returns a clip where each pixel goes from 0 (transparent) to 1 (opaque)
        according to the brush reveal map timing. Used as a mask on the new
        image so the previous image shows through the un-revealed areas.
        """
        reveal_map = cls._generate_brush_reveal_map(width, height)
        feather = cls.BRUSH_FEATHER

        def make_mask_frame(t):
            if t >= draw_duration:
                return np.ones((height, width), dtype=np.float64)
            p = t / draw_duration
            return np.clip(
                (p - reveal_map) / feather, 0.0, 1.0,
            ).astype(np.float64)

        return MoviepyVideoClip(make_mask_frame, duration=duration, is_mask=True)

    @classmethod
    def _generate_brush_reveal_map(cls, width: int, height: int) -> np.ndarray:
        """Generate a reveal map with diagonal brush strokes.

        The image is split into diagonal bands. Each band sweeps along
        its diagonal axis. Band boundaries use coarse, rough noise
        (nearest-neighbor upsampled) to produce irregular, paint-brush-
        like edges with splatter and gaps — not smooth curves.
        """
        n_strokes = cls.BRUSH_STROKE_COUNT
        reveal = np.ones((height, width), dtype=np.float32)

        xs = np.linspace(0, 1, width, dtype=np.float32)
        ys = np.linspace(0, 1, height, dtype=np.float32)
        xg, yg = np.meshgrid(xs, ys)

        diag = 0.35 * xg + 0.65 * yg

        rng = np.random.default_rng()
        time_per_stroke = 1.0 / n_strokes

        full_noise = rng.random((height, width), dtype=np.float32) * 2 - 1
        noise_uint8 = ((full_noise * 0.5 + 0.5) * 255).astype(np.uint8)
        blurred_img = Image.fromarray(noise_uint8, mode="L").filter(
            ImageFilter.GaussianBlur(radius=25)
        )
        blurred = np.array(blurred_img, dtype=np.float32) / 255.0 * 2 - 1
        rough = blurred * 0.30
        diag_rough = diag + rough

        d_min, d_max = diag_rough.min(), diag_rough.max()
        diag_norm = (diag_rough - d_min) / (d_max - d_min)

        for i in range(n_strokes):
            band_lo = i / n_strokes
            band_hi = (i + 1) / n_strokes
            band_mask = (diag_norm >= band_lo) & (diag_norm < band_hi)

            perp = xg - yg
            p_min, p_max = perp[band_mask].min(), perp[band_mask].max()
            if p_max - p_min < 1e-6:
                sweep_val = np.zeros_like(perp)
            else:
                sweep_val = (perp - p_min) / (p_max - p_min)

            if i % 2 == 1:
                sweep_val = 1.0 - sweep_val

            timed = sweep_val * time_per_stroke + i * time_per_stroke
            reveal[band_mask] = timed[band_mask]

        lo, hi = reveal.min(), reveal.max()
        if hi - lo > 1e-6:
            reveal = (reveal - lo) / (hi - lo)

        return reveal

    @staticmethod
    def _blur_image_bytes(img_bytes: bytes, radius: int = 20) -> bytes:
        img = Image.open(io.BytesIO(img_bytes))
        blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
        buf = io.BytesIO()
        blurred.save(buf, format="PNG")
        return buf.getvalue()
