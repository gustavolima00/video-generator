from typing import List, Literal, Optional
from pydantic import Field
from src.entities.base_yaml_model import BaseYAMLModel


class AntiFingerprintConfig(BaseYAMLModel):
    """Subtle, randomized transformations applied to YouTube background clips.

    The goal is to defeat perceptual-hash / fingerprint matching used by
    platforms to flag re-uploaded content. Each output sample is jittered
    randomly inside the configured ranges so two runs produce visually
    distinct frames.
    """

    enabled: bool = Field(True, title="Master toggle for the whole effect chain")
    mirror: bool = Field(True, title="Flip the clip horizontally")
    zoom: float = Field(
        1.04,
        title=(
            "Zoom-in factor (>1 crops a centered region and rescales). "
            "1.0 disables the zoom."
        ),
    )
    brightness_delta: float = Field(
        0.02,
        title=(
            "Maximum random brightness multiplier deviation from 1.0 "
            "(0.04 = ±4%). Set to 0 to disable."
        ),
    )
    contrast_delta: float = Field(
        0,
        title=(
            "Maximum random LumContrast amplitude (sampled in ±value). "
            "0 disables."
        ),
    )
    hue_shift_degrees: float = Field(
        0,
        title=(
            "Maximum random hue rotation in degrees (sampled in ±value). "
            "0 disables."
        ),
    )
    speed_delta: float = Field(
        0.02,
        title=(
            "Maximum random playback speed deviation from 1.0 "
            "(0.02 = ±2%). Audio is replaced downstream so no pitch impact."
        ),
    )


class VideoConfig(BaseYAMLModel):
    watermark_path: Optional[str] = Field(
        None, title="Path to the watermark image file"
    )
    call_to_action_path: Optional[str] = Field(
        None, title="Path to the call-to-action overlay image"
    )
    end_silece_seconds: int = Field(3, title="End silence seconds")
    padding: int = Field(60, title="Padding")
    cover_width_ratio: float = Field(
        0.82,
        title=(
            "Cover width as a fraction of the video width. Kept below 1 so the "
            "cover reads as a card and leaves room for the captions underneath "
            "it (see CaptionsConfig.vertical_position)."
        ),
    )
    cover_duration: float = Field(
        0.5,
        title=(
            "How long the cover stays on screen, in seconds. It overlays the "
            "opening of the story — the narration runs from the first frame, so "
            "this does not add to the video duration."
        ),
    )
    width: int = Field(1080, title="Width of the video")
    height: int = Field(1920, title="Height of the video")
    fps: int = Field(
        30,
        title=(
            "Output frame rate for the YouTube-background videos. Without it "
            "the render inherits the frame rate of whichever background got "
            "downloaded — usually 60, which doubles the frame count and the "
            "render time. (Image-story videos render at their own fixed 24.)"
        ),
    )
    youtube_channel_url: str = Field(
        "https://www.youtube.com/@FoodieBoyKR",
        title="Fallback YouTube channel url",
    )
    youtube_channel_urls: List[str] = Field(
        default_factory=list,
        title="YouTube channel urls available for background clips",
    )
    youtube_channel_strategy: Literal["random", "all"] = Field(
        "random",
        title=(
            "How to use configured YouTube channels: random selects one channel, "
            "all merges candidates from every channel."
        ),
    )
    youtube_pool_size: int = Field(
        50,
        title="Only consider the N newest videos per selected channel. 0 = all.",
    )
    youtube_surface: Literal["videos", "shorts"] = Field(
        "videos",
        title="Which YouTube channel surface to use for background clips",
    )
    ffmpeg_params: List[str] = Field([], title="ffmpeg params")
    draw_transition_duration: float = Field(
        1.0,
        title="Duration (seconds) of the draw-in reveal effect. Set to 0 to use a simple crossfade instead.",
    )
    anti_fingerprint: AntiFingerprintConfig = Field(
        default_factory=AntiFingerprintConfig,
        title="Subtle randomized transforms to evade content-fingerprint detection",
    )
