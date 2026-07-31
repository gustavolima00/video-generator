"""The cover holds the screen alone and the story waits for it.

Everything that is timed against the narration — captions, image schedule,
call-to-action — has to move by the same amount, or the subtitles drift out
of sync with the voice.
"""

from types import SimpleNamespace

from src.entities.captions import CaptionSegment, Captions
from src.entities.configs.services.video import VideoConfig
from src.entities.image_story import ImageStory, StoryImage
from src.services.video_service import VideoService


def _image_story() -> ImageStory:
    return ImageStory(
        images=[
            StoryImage(start_time=0.0, description="a", prompt="a"),
            StoryImage(start_time=4.0, description="b", prompt="b"),
        ],
        introduction_end_time=0.5,
        call_to_action_start_time=30.0,
    )


def test_captions_shift_by_the_cover_duration():
    captions = Captions(
        segments=[
            CaptionSegment(start=0.0, end=0.4, text="Depois"),
            CaptionSegment(start=0.4, end=0.9, text="de"),
        ]
    )

    shifted = captions.shifted(1.5)

    assert [(s.start, s.end) for s in shifted.segments] == [(1.5, 1.9), (1.9, 2.4)]
    # original untouched
    assert captions.segments[0].start == 0.0


def test_caption_clip_shift_keeps_font_and_leaves_original_alone():
    clip = SimpleNamespace(
        captions=Captions(segments=[CaptionSegment(start=2.0, end=2.5, text="oi")]),
        font_path="/tmp/font.ttf",
    )

    shifted = VideoService._shift_captions(clip, 1.5)

    assert shifted.font_path == "/tmp/font.ttf"
    assert shifted.captions.segments[0].start == 3.5
    assert clip.captions.segments[0].start == 2.0


def test_image_story_shift_pins_first_image_under_the_cover():
    story = _image_story()

    shifted = VideoService._shift_image_story(story, 1.5)

    # The first image sits under the (blurred) cover from t=0, the rest move.
    assert [img.start_time for img in shifted.images] == [0.0, 5.5]
    assert shifted.call_to_action_start_time == 31.5
    # original untouched
    assert [img.start_time for img in story.images] == [0.0, 4.0]
    assert story.call_to_action_start_time == 30.0


def test_default_cover_duration_is_the_configured_hold():
    assert VideoConfig().cover_duration == 1.5
