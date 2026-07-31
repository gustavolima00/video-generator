import asyncio
import json
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Optional

from ..entities.captions import Captions
from ..entities.cover import RedditCover
from ..entities.editor import image_clip
from ..entities.image_story import ImageStory
from ..entities.editor.audio_clip import AudioClip
from ..entities.editor.captions_clip import CaptionsClip
from ..entities.language import Language
from ..entities.reddit_post import RedditPost
from ..proxies.interfaces import IImageGeneratorProxy, ILLMProxy, IRedditProxy
from .captions_service import CaptionsResult, CaptionsService
from .cover_service import CoverResult, CoverService
from .speech_service import SpeechResult, SpeechService
from .text_censor import TextCensor
from .video_service import VideoService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intermediate data-classes (used by the checkpoint flow)
# ---------------------------------------------------------------------------


@dataclass
class StoryScript:
    title: str
    part1: str
    part2: str
    narrator_gender: str
    resolved_gender: Literal["male", "female"]


@dataclass
class AudioPair:
    part1: SpeechResult
    part2: SpeechResult


@dataclass
class CaptionsPair:
    part1: CaptionsResult
    part2: CaptionsResult
    part1_data: list[dict]
    part2_data: list[dict]
    raw_part1_data: list[dict]
    raw_part2_data: list[dict]


@dataclass
class CharacterSheet:
    characters: list[dict]
    reference_images: dict[str, bytes]


@dataclass
class ImageStoryPair:
    part1: ImageStory
    part2: ImageStory
    generated_images_1: list[bytes]
    generated_images_2: list[bytes]


@dataclass
class VideoPair:
    part1_video: bytes
    part2_video: bytes


# ---------------------------------------------------------------------------
# Final output data-classes
# ---------------------------------------------------------------------------


@dataclass
class TwoPartVideoResult:
    """All generated artifacts as in-memory bytes / strings."""

    part1_video: bytes
    part2_video: bytes
    story_md: str
    original_post_md: str
    audio_part1: bytes
    audio_part2: bytes
    captions_part1_json: str
    captions_part2_json: str
    cover_part1_png: Optional[bytes] = None
    cover_part2_png: Optional[bytes] = None


@dataclass
class PreparedStory:
    """Intermediate result from Stage 1: scrape + LLM story generation.

    Contains everything needed to produce the video without further LLM calls.
    """

    post: RedditPost
    script_text: str
    story_title: str
    narrator_gender: str
    resolved_gender: Literal["male", "female"]
    original_post_md: str


@dataclass
class SingleVideoResult:
    """All generated artifacts for a single satisfying-background video."""

    video: bytes
    story_md: str
    original_post_md: str
    audio: bytes
    captions_json: str
    localized_title: str
    cover_png: Optional[bytes] = None


@dataclass
class ImageStoryVideoResult:
    """All generated artifacts for an image-story video."""

    part1_video: bytes
    part2_video: bytes
    story_md: str
    original_post_md: str
    audio_part1: bytes
    audio_part2: bytes
    captions_part1_json: str
    captions_part2_json: str
    image_story_part1_json: str
    image_story_part2_json: str
    cover_part1_png: Optional[bytes] = None
    cover_part2_png: Optional[bytes] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RedditVideoService:
    """Generates Reddit-story videos with either YouTube backgrounds or AI-generated images."""

    def __init__(
        self,
        reddit_proxy: IRedditProxy,
        llm_proxy: ILLMProxy,
        image_generation_proxy: IImageGeneratorProxy,
        speech_service: SpeechService,
        captions_service: CaptionsService,
        cover_service: CoverService,
        video_service: VideoService,
        portrait_generation_proxy: Optional[IImageGeneratorProxy] = None,
        history_adaptation_llm_proxy: Optional[ILLMProxy] = None,
        text_censor: Optional[TextCensor] = None,
    ) -> None:
        self._reddit_proxy = reddit_proxy
        self._llm_proxy = llm_proxy
        self._history_adaptation_llm_proxy = history_adaptation_llm_proxy or llm_proxy
        self._image_generation_proxy = image_generation_proxy
        self._portrait_proxy = portrait_generation_proxy or image_generation_proxy
        self._speech_service = speech_service
        self._captions_service = captions_service
        self._cover_service = cover_service
        self._video_service = video_service
        self._text_censor = text_censor or TextCensor()

    # ------------------------------------------------------------------
    # Step methods (used individually by the interactive bot)
    # ------------------------------------------------------------------

    def scrape_post(self, url: str) -> RedditPost:
        return self._reddit_proxy.get_reddit_post(url)

    async def generate_script(
        self,
        post: RedditPost,
        language: Language = Language.PORTUGUESE,
        speech_gender: Optional[Literal["male", "female"]] = None,
    ) -> StoryScript:
        story = await self._history_adaptation_llm_proxy.generate_two_part_story(
            title=post.title,
            content=post.content,
            target_language=language,
        )
        narrator_gender = story.get("narrator_gender", "unknown")
        resolved_gender: Literal["male", "female"] = speech_gender or (
            narrator_gender if narrator_gender in ("male", "female") else "male"
        )
        return StoryScript(
            title=story.get("title", post.title),
            part1=story["part1"],
            part2=story["part2"],
            narrator_gender=narrator_gender,
            resolved_gender=resolved_gender,
        )

    async def revise_script(
        self,
        script: StoryScript,
        feedback: str,
        language: Language = Language.PORTUGUESE,
    ) -> StoryScript:
        revised = await self._history_adaptation_llm_proxy.revise_story(
            current_script={
                "title": script.title,
                "part1": script.part1,
                "part2": script.part2,
                "narrator_gender": script.narrator_gender,
            },
            feedback=feedback,
            target_language=language,
        )
        return StoryScript(
            title=revised.get("title", script.title),
            part1=revised["part1"],
            part2=revised["part2"],
            narrator_gender=revised.get("narrator_gender", script.narrator_gender),
            resolved_gender=script.resolved_gender,
        )

    async def generate_audio(
        self,
        script: StoryScript,
        speech_rate: float = 1.0,
        language: Language = Language.PORTUGUESE,
    ) -> AudioPair:
        part1 = await self._speech_service.generate_speech(
            text=script.part1,
            gender=script.resolved_gender,
            rate=speech_rate,
            language=language,
        )
        part2 = await self._speech_service.generate_speech(
            text=script.part2,
            gender=script.resolved_gender,
            rate=speech_rate,
            language=language,
        )
        return AudioPair(part1=part1, part2=part2)

    async def generate_captions_pair(
        self,
        audio: AudioPair,
        script: StoryScript,
        language: Language = Language.PORTUGUESE,
    ) -> CaptionsPair:
        cap1 = await self._captions_service.generate_captions(
            audio_bytes=audio.part1.bytes,
            enhance_captions=True,
            language=language,
            base_text=script.part1,
        )
        cap2 = await self._captions_service.generate_captions(
            audio_bytes=audio.part2.bytes,
            enhance_captions=True,
            language=language,
            base_text=script.part2,
        )
        raw1 = [
            {"word": s.text, "start": s.start, "end": s.end}
            for s in cap1.captions.segments
        ]
        raw2 = [
            {"word": s.text, "start": s.start, "end": s.end}
            for s in cap2.captions.segments
        ]

        # Censor exported JSON data (visible text only; raw data keeps originals for timing).
        data1 = self._text_censor.censor_word_dicts(self._strip_introduction(raw1))
        data2 = self._text_censor.censor_word_dicts(self._strip_introduction(raw2))

        # Censor on-screen caption segments inside the clips.
        cap1.clip.captions = Captions(
            segments=self._text_censor.censor_segments(cap1.captions.segments)
        )
        cap2.clip.captions = Captions(
            segments=self._text_censor.censor_segments(cap2.captions.segments)
        )

        return CaptionsPair(
            part1=cap1,
            part2=cap2,
            part1_data=data1,
            part2_data=data2,
            raw_part1_data=raw1,
            raw_part2_data=raw2,
        )

    async def generate_cover(
        self, post: RedditPost, script: StoryScript, part_label: str = ""
    ) -> CoverResult:
        title = f"{script.title} - {part_label}" if part_label else script.title
        return await self._cover_service.generate_cover(
            RedditCover(
                title=self._text_censor.censor(title),
                community=post.community,
                author=post.author,
                image_url=post.community_url_photo,
            )
        )

    async def generate_cover_pair(
        self, post: RedditPost, script: StoryScript
    ) -> tuple[CoverResult, CoverResult]:
        cover1, cover2 = await asyncio.gather(
            self.generate_cover(post, script, part_label="Parte 1"),
            self.generate_cover(post, script, part_label="Parte 2"),
        )
        return cover1, cover2

    async def compose_two_part_video(
        self,
        audio: AudioPair,
        captions: CaptionsPair,
        cover_part1: CoverResult,
        cover_part2: CoverResult,
        low_quality: bool = False,
    ) -> VideoPair:
        video_bytes_1 = await self._render_video_to_bytes(
            speech=audio.part1.clip,
            captions_clip_obj=captions.part1.clip,
            cover=cover_part1.clip,
            low_quality=low_quality,
        )
        video_bytes_2 = await self._render_video_to_bytes(
            speech=audio.part2.clip,
            captions_clip_obj=captions.part2.clip,
            cover=cover_part2.clip,
            low_quality=low_quality,
        )
        return VideoPair(part1_video=video_bytes_1, part2_video=video_bytes_2)

    async def generate_characters(
        self,
        script: StoryScript,
        language: Language = Language.PORTUGUESE,
    ) -> CharacterSheet:
        """LLM extracts characters, then generate a reference portrait for the protagonist only."""
        characters = await self._llm_proxy.generate_characters(
            title=script.title,
            part1=script.part1,
            part2=script.part2,
            target_language=language,
        )
        config = self._video_service._video_config
        img_w, img_h = config.width, config.height

        reference_images: dict[str, bytes] = {}
        if characters:
            protagonist = characters[0]
            prompt = protagonist["visual_prompt"] + self.PORTRAIT_SUFFIX
            result = self._portrait_proxy.generate_image(
                prompt=prompt,
                negative_prompt=self.SFW_NEGATIVE_PROMPT,
                width=img_w,
                height=img_h,
                num_images=1,
            )
            reference_images[protagonist["name"]] = result[0]

        return CharacterSheet(characters=characters, reference_images=reference_images)

    async def generate_image_stories(
        self,
        script: StoryScript,
        captions: CaptionsPair,
        character_sheet: CharacterSheet | None = None,
    ) -> ImageStoryPair:
        """LLM plans timed image descriptions + prompts for both parts."""
        intro1, cta1, offset1, content1 = self._compute_content_boundaries(
            captions.raw_part1_data
        )
        image_story_1 = await self._llm_proxy.generate_image_story(
            story_text=script.part1,
            transcription=content1,
            introduction_end_time=intro1,
            call_to_action_start_time=cta1,
        )
        self._shift_images_back(image_story_1, offset1)
        style_context = self._extract_style_context(image_story_1)

        intro2, cta2, offset2, content2 = self._compute_content_boundaries(
            captions.raw_part2_data
        )
        image_story_2 = await self._llm_proxy.generate_image_story(
            story_text=script.part2,
            transcription=content2,
            style_context=style_context,
            introduction_end_time=intro2,
            call_to_action_start_time=cta2,
        )
        self._shift_images_back(image_story_2, offset2)
        config = self._video_service._video_config
        img_w, img_h = config.width, config.height

        generated_1 = self._generate_images_for_story(image_story_1, img_w, img_h)
        generated_2 = self._generate_images_for_story(image_story_2, img_w, img_h)

        return ImageStoryPair(
            part1=image_story_1,
            part2=image_story_2,
            generated_images_1=generated_1,
            generated_images_2=generated_2,
        )

    async def compose_image_story_video(
        self,
        audio: AudioPair,
        captions: CaptionsPair,
        image_stories: ImageStoryPair,
        cover_part1: CoverResult,
        cover_part2: CoverResult,
        low_quality: bool = False,
    ) -> VideoPair:
        video_bytes_1 = await self._render_image_story_to_bytes(
            audio=audio.part1.clip,
            image_story=image_stories.part1,
            generated_images=image_stories.generated_images_1,
            cover=cover_part1.clip,
            captions=captions.part1.clip,
            low_quality=low_quality,
        )
        video_bytes_2 = await self._render_image_story_to_bytes(
            audio=audio.part2.clip,
            image_story=image_stories.part2,
            generated_images=image_stories.generated_images_2,
            cover=cover_part2.clip,
            captions=captions.part2.clip,
            low_quality=low_quality,
        )
        return VideoPair(part1_video=video_bytes_1, part2_video=video_bytes_2)

    # ------------------------------------------------------------------
    # Public API (monolithic, kept for backward compat)
    # ------------------------------------------------------------------

    async def generate_two_part_history_video(
        self,
        *,
        post_url: str,
        language: Language = Language.PORTUGUESE,
        speech_gender: Optional[Literal["male", "female"]] = None,
        speech_rate: float = 1.0,
        low_quality: bool = False,
    ) -> TwoPartVideoResult:
        """Full pipeline: scrape -> story -> speech -> captions -> videos."""

        post = self.scrape_post(post_url)
        original_post_md = f"# {post.title}\n\n{post.content}\n"

        script = await self.generate_script(post, language, speech_gender)
        audio = await self.generate_audio(script, speech_rate, language)
        captions = await self.generate_captions_pair(audio, script, language)
        cover1, cover2 = await self.generate_cover_pair(post, script)
        videos = await self.compose_two_part_video(
            audio, captions, cover1, cover2, low_quality
        )

        story_md = f"# {script.title}\n\n"
        story_md += f"**Narrator gender:** {script.narrator_gender} → resolved: {script.resolved_gender}\n\n"
        story_md += f"## Part 1\n\n{script.part1}\n\n"
        story_md += f"## Part 2\n\n{script.part2}\n"

        return TwoPartVideoResult(
            part1_video=videos.part1_video,
            part2_video=videos.part2_video,
            story_md=story_md,
            original_post_md=original_post_md,
            audio_part1=audio.part1.bytes,
            audio_part2=audio.part2.bytes,
            captions_part1_json=json.dumps(
                captions.part1_data, ensure_ascii=False, indent=2
            ),
            captions_part2_json=json.dumps(
                captions.part2_data, ensure_ascii=False, indent=2
            ),
            cover_part1_png=cover1.bytes,
            cover_part2_png=cover2.bytes,
        )

    async def prepare_satisfying_story(
        self,
        *,
        post_url: str,
        language: Language = Language.PORTUGUESE,
        speech_gender: Optional[Literal["male", "female"]] = None,
    ) -> PreparedStory:
        """Stage 1: scrape + LLM story generation (the most failure-prone step)."""

        post = self.scrape_post(post_url)
        original_post_md = f"# {post.title}\n\n{post.content}\n"

        story = await self._history_adaptation_llm_proxy.generate_story(
            title=post.title,
            content=post.content,
            target_language=language,
        )
        script_text: str = story["script"]

        narrator_gender = story.get("narrator_gender", "unknown")
        resolved_gender: Literal["male", "female"] = speech_gender or (
            narrator_gender if narrator_gender in ("male", "female") else "male"
        )

        return PreparedStory(
            post=post,
            script_text=script_text,
            story_title=story.get("title", post.title),
            narrator_gender=narrator_gender,
            resolved_gender=resolved_gender,
            original_post_md=original_post_md,
        )

    async def generate_satisfying_video_from_story(
        self,
        prepared: PreparedStory,
        *,
        speech_rate: float = 1.0,
        low_quality: bool = False,
        language: Language = Language.PORTUGUESE,
    ) -> SingleVideoResult:
        """Stage 2: speech, captions, cover, video composition from an already-prepared story."""

        speech_result = await self._speech_service.generate_speech(
            text=prepared.script_text,
            gender=prepared.resolved_gender,
            rate=speech_rate,
            language=language,
        )

        captions_result = await self._captions_service.generate_captions(
            audio_bytes=speech_result.bytes,
            enhance_captions=True,
            language=language,
            base_text=prepared.script_text,
        )

        segments_data = [
            {"word": s.text, "start": s.start, "end": s.end}
            for s in captions_result.captions.segments
        ]

        cta_start_time = self._compute_satisfying_cta_start(segments_data)

        # The title is not narrated (it lives on the cover), so the whole
        # transcription is story content — nothing is trimmed. The cover gets
        # its own slot at the front of the video instead.
        captions_data = self._text_censor.censor_word_dicts(segments_data)

        captions_result.clip.captions = Captions(
            segments=self._text_censor.censor_segments(
                captions_result.clip.captions.segments
            )
        )

        cover_result = await self._cover_service.generate_cover(
            RedditCover(
                title=self._text_censor.censor(prepared.story_title),
                community=prepared.post.community,
                author=prepared.post.author,
                image_url=prepared.post.community_url_photo,
            )
        )

        video_bytes = await self._render_video_to_bytes(
            speech=speech_result.clip,
            captions_clip_obj=captions_result.clip,
            cover=cover_result.clip,
            low_quality=low_quality,
            cta_start=cta_start_time,
        )

        story_md = f"# {prepared.story_title}\n\n"
        story_md += f"**Narrator gender:** {prepared.narrator_gender} → resolved: {prepared.resolved_gender}\n\n"
        story_md += f"{prepared.script_text}\n"

        return SingleVideoResult(
            video=video_bytes,
            story_md=story_md,
            original_post_md=prepared.original_post_md,
            audio=speech_result.bytes,
            captions_json=json.dumps(captions_data, ensure_ascii=False, indent=2),
            localized_title=prepared.story_title,
            cover_png=cover_result.bytes,
        )

    async def generate_satisfying_video(
        self,
        *,
        post_url: str,
        language: Language = Language.PORTUGUESE,
        speech_gender: Optional[Literal["male", "female"]] = None,
        speech_rate: float = 1.0,
        low_quality: bool = False,
    ) -> SingleVideoResult:
        """Full pipeline: scrape -> single story -> speech -> captions -> satisfying background video."""

        prepared = await self.prepare_satisfying_story(
            post_url=post_url,
            language=language,
            speech_gender=speech_gender,
        )
        return await self.generate_satisfying_video_from_story(
            prepared,
            speech_rate=speech_rate,
            low_quality=low_quality,
            language=language,
        )

    async def generate_image_story_video(
        self,
        *,
        post_url: str,
        language: Language = Language.PORTUGUESE,
        speech_gender: Optional[Literal["male", "female"]] = None,
        speech_rate: float = 1.0,
        low_quality: bool = False,
    ) -> ImageStoryVideoResult:
        """Full pipeline: scrape → story → speech → captions → image story → images → video."""

        # 1. Scrape
        post = self._reddit_proxy.get_reddit_post(post_url)
        original_post_md = f"# {post.title}\n\n{post.content}\n"

        # 2. LLM: two-part story
        story = await self._history_adaptation_llm_proxy.generate_two_part_story(
            title=post.title,
            content=post.content,
            target_language=language,
        )
        part1_text: str = story["part1"]
        part2_text: str = story["part2"]

        narrator_gender = story.get("narrator_gender", "unknown")
        resolved_gender: Literal["male", "female"] = speech_gender or (
            narrator_gender if narrator_gender in ("male", "female") else "male"
        )

        # 3. Speech
        speech_result_1 = await self._speech_service.generate_speech(
            text=part1_text,
            gender=resolved_gender,
            rate=speech_rate,
            language=language,
        )
        speech_result_2 = await self._speech_service.generate_speech(
            text=part2_text,
            gender=resolved_gender,
            rate=speech_rate,
            language=language,
        )

        # 4. Captions
        captions_result_1 = await self._captions_service.generate_captions(
            audio_bytes=speech_result_1.bytes,
            enhance_captions=True,
            language=language,
            base_text=part1_text,
        )
        captions_result_2 = await self._captions_service.generate_captions(
            audio_bytes=speech_result_2.bytes,
            enhance_captions=True,
            language=language,
            base_text=part2_text,
        )

        raw_captions_1 = [
            {"word": s.text, "start": s.start, "end": s.end}
            for s in captions_result_1.captions.segments
        ]
        raw_captions_2 = [
            {"word": s.text, "start": s.start, "end": s.end}
            for s in captions_result_2.captions.segments
        ]

        # Censor exported JSON data; raw_captions_* keep originals for image-story timing.
        captions_1_data = self._text_censor.censor_word_dicts(
            self._strip_introduction(raw_captions_1)
        )
        captions_2_data = self._text_censor.censor_word_dicts(
            self._strip_introduction(raw_captions_2)
        )

        # Censor on-screen caption segments inside the clips.
        captions_result_1.clip.captions = Captions(
            segments=self._text_censor.censor_segments(captions_result_1.captions.segments)
        )
        captions_result_2.clip.captions = Captions(
            segments=self._text_censor.censor_segments(captions_result_2.captions.segments)
        )

        config = self._video_service._video_config
        img_w, img_h = config.width, config.height

        # 5. LLM: generate image stories from captions
        intro1, cta1, offset1, content1 = self._compute_content_boundaries(
            raw_captions_1
        )
        image_story_1 = await self._llm_proxy.generate_image_story(
            story_text=part1_text,
            transcription=content1,
            introduction_end_time=intro1,
            call_to_action_start_time=cta1,
        )
        self._shift_images_back(image_story_1, offset1)

        style_context = self._extract_style_context(image_story_1)

        intro2, cta2, offset2, content2 = self._compute_content_boundaries(
            raw_captions_2
        )
        image_story_2 = await self._llm_proxy.generate_image_story(
            story_text=part2_text,
            transcription=content2,
            style_context=style_context,
            introduction_end_time=intro2,
            call_to_action_start_time=cta2,
        )
        self._shift_images_back(image_story_2, offset2)

        # 6. Generate images
        generated_images_1 = self._generate_images_for_story(
            image_story_1, img_w, img_h,
        )
        generated_images_2 = self._generate_images_for_story(
            image_story_2, img_w, img_h,
        )

        # 8. Covers
        base_title = story.get("title", post.title)
        censored_base_title = self._text_censor.censor(base_title)
        cover_result_1, cover_result_2 = await asyncio.gather(
            self._cover_service.generate_cover(
                RedditCover(
                    title=f"{censored_base_title} - Parte 1",
                    community=post.community,
                    author=post.author,
                    image_url=post.community_url_photo,
                )
            ),
            self._cover_service.generate_cover(
                RedditCover(
                    title=f"{censored_base_title} - Parte 2",
                    community=post.community,
                    author=post.author,
                    image_url=post.community_url_photo,
                )
            ),
        )

        # 9. Build story markdown
        story_md = f"# {story.get('title', 'Untitled')}\n\n"
        story_md += (
            f"**Narrator gender:** {narrator_gender} → resolved: {resolved_gender}\n\n"
        )
        story_md += f"## Part 1\n\n{part1_text}\n\n"
        story_md += f"## Part 2\n\n{part2_text}\n"

        # 10. Compose videos
        video_bytes_1 = await self._render_image_story_to_bytes(
            audio=speech_result_1.clip,
            image_story=image_story_1,
            generated_images=generated_images_1,
            cover=cover_result_1.clip,
            captions=captions_result_1.clip,
            low_quality=low_quality,
        )
        video_bytes_2 = await self._render_image_story_to_bytes(
            audio=speech_result_2.clip,
            image_story=image_story_2,
            generated_images=generated_images_2,
            cover=cover_result_2.clip,
            captions=captions_result_2.clip,
            low_quality=low_quality,
        )

        return ImageStoryVideoResult(
            part1_video=video_bytes_1,
            part2_video=video_bytes_2,
            story_md=story_md,
            original_post_md=original_post_md,
            audio_part1=speech_result_1.bytes,
            audio_part2=speech_result_2.bytes,
            captions_part1_json=json.dumps(
                captions_1_data, ensure_ascii=False, indent=2
            ),
            captions_part2_json=json.dumps(
                captions_2_data, ensure_ascii=False, indent=2
            ),
            image_story_part1_json=image_story_1.model_dump_json(indent=2),
            image_story_part2_json=image_story_2.model_dump_json(indent=2),
            cover_part1_png=cover_result_1.bytes,
            cover_part2_png=cover_result_2.bytes,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    SFW_NEGATIVE_PROMPT = (
        "nsfw, nudity, sexual, gore, violence, blood, "
        "explicit, inappropriate, offensive"
    )

    PORTRAIT_SUFFIX = ", character portrait, centered, neutral background"

    PART1_CTA = "Curta e me siga para a parte 2."
    PART2_CTA = "Curta, me siga e deixe nos comentários"
    PART_MARKERS = {"part", "parte", "partie", "teil"}
    CTA_START_WORDS = {
        "curta",
        "like",
        "dale",
        "deja",
        "laisse",
        "lascia",
        "gib",
    }

    @classmethod
    def _normalize_marker_word(cls, word: str) -> str:
        return word.strip().lower().strip(".,!?;:¿¡")

    def _compute_content_boundaries(
        self, raw_transcription: list[dict]
    ) -> tuple[float, float, float, list[dict]]:
        """Slice content from raw transcription and shift timestamps to zero-based.

        The title and "Parte N." marker are no longer narrated (they live on the
        cover image), so by default nothing is stripped and the cover simply
        overlays the first ``cover_duration`` seconds of the story. A leading
        "Parte N." marker is still detected for backward compatibility with older
        scripts that embedded it in the narration.

        Returns (intro_end_time, cta_start_time, offset, zero_based_content).
        """
        n = len(raw_transcription)
        cover_duration = float(self._video_service._video_config.cover_duration)

        # --- intro: default keeps the first word (no marker to strip) ---
        intro_end_idx = -1
        intro_end_time = cover_duration
        for i, w in enumerate(raw_transcription[:30]):
            word = w.get("word", "").strip()
            if re.match(r"^\d+[.,]?$", word):
                prev = (
                    self._normalize_marker_word(raw_transcription[i - 1].get("word", ""))
                    if i > 0
                    else ""
                )
                if prev in self.PART_MARKERS:
                    intro_end_idx = i
                    intro_end_time = w["end"]
                    break

        # --- CTA: find "Curta" in the last 20 words ---
        cta_start_idx = n
        cta_start_time = (
            raw_transcription[-3]["start"] if n > 3 else intro_end_time + 10
        )
        search_start = max(intro_end_idx + 1, n - 20)
        for i in range(search_start, n):
            word = self._normalize_marker_word(raw_transcription[i].get("word", ""))
            if word in self.CTA_START_WORDS:
                cta_start_idx = i
                cta_start_time = raw_transcription[i]["start"]
                break

        # --- slice content ---
        content = raw_transcription[intro_end_idx + 1 : cta_start_idx]

        if len(content) < 5:
            logger.warning(
                "Content slice too short (%d words), falling back to full transcription",
                len(content),
            )
            content = list(raw_transcription)
            intro_end_time = cover_duration
            cta_start_time = (
                content[-3]["start"] if len(content) > 3 else intro_end_time + 10
            )

        # --- shift to zero-based ---
        offset = content[0]["start"]
        zero_based = [
            {
                "word": w["word"],
                "start": round(w["start"] - offset, 3),
                "end": round(w["end"] - offset, 3),
            }
            for w in content
        ]

        return intro_end_time, cta_start_time, offset, zero_based

    @staticmethod
    def _shift_images_back(image_story: ImageStory, offset: float) -> None:
        """Shift all image start_times back by offset, then pin first image to 0.0."""
        for img in image_story.images:
            img.start_time = round(img.start_time + offset, 3)
        image_story.images[0].start_time = 0.0

    @staticmethod
    def _compute_satisfying_cta_start(segments: list[dict]) -> float:
        """Find when the call-to-action starts in a single-story video.

        The title is not narrated, so there is no spoken intro to detect —
        only the CTA, found by looking for "curta" in the last ~20 words.
        Times are relative to the narration; the renderer shifts them when it
        puts the cover in front.
        """
        n = len(segments)
        if n == 0:
            return 0.0

        cta_start_time = segments[-3]["start"] if n > 3 else segments[0]["start"]
        for i in range(max(0, n - 20), n):
            word = RedditVideoService._normalize_marker_word(segments[i].get("word", ""))
            if word in RedditVideoService.CTA_START_WORDS:
                return segments[i]["start"]

        return cta_start_time

    @staticmethod
    def _strip_introduction(transcription: list[dict]) -> list[dict]:
        """Remove words up to and including 'Parte N.' from the transcription.

        The title and "Parte N." are no longer narrated (they live on the cover),
        so for current scripts no marker is found and the full transcription is
        returned unchanged. Kept for backward compatibility with older scripts
        that still embed the spoken marker.
        """
        parte_idx = -1
        for i, w in enumerate(transcription):
            word = w.get("word", "").strip()
            if re.match(r"^\d+[.,]?$", word):
                prev = (
                    RedditVideoService._normalize_marker_word(
                        transcription[i - 1].get("word", "")
                    )
                    if i > 0
                    else ""
                )
                if prev in RedditVideoService.PART_MARKERS:
                    parte_idx = i
                    break
        if parte_idx >= 0:
            return transcription[parte_idx + 1 :]
        return transcription

    @staticmethod
    def _extract_style_context(image_story) -> str:
        """Build a style guide from part 1's image prompts so part 2 stays consistent."""
        prompts = [img.prompt for img in image_story.images[:5]]
        lines = [f"- Image {i + 1}: {p}" for i, p in enumerate(prompts)]
        return (
            "The following image prompts were used in Part 1. "
            "Reuse the exact same character descriptions (age, hair, build, clothing) "
            "and the exact same art style suffix for all prompts in Part 2.\n\n"
            + "\n".join(lines)
        )

    IMAGE_GEN_MAX_WORKERS = 5

    def _generate_images_for_story(
        self,
        image_story,
        width: int,
        height: int,
    ) -> list:
        def _generate_single(img_def):
            result = self._image_generation_proxy.generate_image(
                prompt=img_def.prompt,
                negative_prompt=self.SFW_NEGATIVE_PROMPT,
                width=width,
                height=height,
                num_images=1,
            )
            return result[0]

        with ThreadPoolExecutor(max_workers=self.IMAGE_GEN_MAX_WORKERS) as pool:
            futures = [pool.submit(_generate_single, img_def) for img_def in image_story.images]
            return [f.result() for f in futures]

    async def _render_image_story_to_bytes(
        self,
        *,
        audio: AudioClip,
        image_story,
        generated_images: list,
        cover: Optional[image_clip.ImageClip],
        captions: Optional[CaptionsClip],
        low_quality: bool,
    ) -> bytes:
        final_video = self._video_service.generate_image_story_video(
            audio=audio,
            image_story=image_story,
            generated_images=generated_images,
            cover=cover,
            captions=captions,
            low_quality=low_quality,
        )
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        def _write_and_read() -> bytes:
            try:
                final_video.clip.write_videofile(
                    tmp_path,
                    fps=24,
                    ffmpeg_params=self._video_service._video_config.ffmpeg_params,
                )
                with open(tmp_path, "rb") as f:
                    return f.read()
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        return await asyncio.to_thread(_write_and_read)

    async def _render_video_to_bytes(
        self,
        *,
        speech,
        captions_clip_obj: Optional[CaptionsClip],
        cover: Optional[image_clip.ImageClip],
        low_quality: bool,
        cta_start: float = 0,
    ) -> bytes:
        """Compile a single video and return it as bytes."""

        # Download YouTube compilation background
        compilation_result = await self._video_service.create_youtube_video_compilation(
            min_duration=speech.clip.duration,
            low_quality=low_quality,
        )
        background_video = compilation_result.clip

        if background_video is None:
            raise RuntimeError("Failed to create background video compilation.")

        # Compose
        final_video = self._video_service.generate_video(
            audio=speech,
            background_video=background_video,
            low_quality=low_quality,
            cover=cover,
            captions=captions_clip_obj,
            cta_start=cta_start,
        )

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        def _write_and_read() -> bytes:
            try:
                final_video.clip.write_videofile(
                    tmp_path,
                    ffmpeg_params=self._video_service._video_config.ffmpeg_params,
                )
                with open(tmp_path, "rb") as f:
                    return f.read()
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        return await asyncio.to_thread(_write_and_read)
