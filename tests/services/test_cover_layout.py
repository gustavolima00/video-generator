"""The cover overlays the opening without hiding the subtitles.

The narration runs from the first frame, so the cover has to share the screen
with the captions: it is narrower than the video and centred, and the captions
sit below it.
"""

from src.entities.configs.services.captions import CaptionsConfig
from src.entities.configs.services.video import VideoConfig


def _cover_band(config: VideoConfig, cover_aspect: float) -> tuple[float, float]:
    """Return the (top, bottom) of the centred cover as fractions of height.

    *cover_aspect* is width/height of the cover image — 1.544 for the real
    Reddit cover card measured off a rendered video.
    """
    cover_w = config.width * config.cover_width_ratio
    cover_h = cover_w / cover_aspect
    top = (config.height - cover_h) / 2
    return top / config.height, (top + cover_h) / config.height


def test_captions_start_below_the_cover():
    video, captions = VideoConfig(), CaptionsConfig()
    _, cover_bottom = _cover_band(video, cover_aspect=1.544)

    assert captions.vertical_position > cover_bottom, (
        f"captions start at {captions.vertical_position:.3f} but the cover "
        f"runs to {cover_bottom:.3f} — they would overlap"
    )


def test_captions_stay_on_screen():
    video, captions = VideoConfig(), CaptionsConfig()
    # A caption block is roughly the font plus its margins, top and bottom.
    block = captions.font_size + 2 * captions.marging
    bottom = captions.vertical_position + block / video.height

    assert bottom < 1.0, f"caption block would run off the bottom ({bottom:.3f})"


def test_cover_is_narrower_than_the_frame():
    assert 0 < VideoConfig().cover_width_ratio < 1


def test_cover_duration_does_not_delay_the_story():
    # 0.5s of overlay, not 0.5s of silence: the value is a display duration.
    assert VideoConfig().cover_duration == 0.5


def test_default_fps_does_not_inherit_the_60fps_backgrounds():
    assert VideoConfig().fps == 30
