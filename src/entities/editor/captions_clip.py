import tempfile
from typing import List
from moviepy import TextClip
from moviepy.video.fx import CrossFadeIn, CrossFadeOut
from src.entities.captions import CaptionSegment, Captions
from src.entities.config import CaptionsConfig


class CaptionsClip:
    def __init__(
        self,
        captions: Captions,
        config: CaptionsConfig = CaptionsConfig(),
        font_bytes: bytes = None,
    ):
        self.captions = captions
        self.config = config
        with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmpfile:
            tmpfile.write(font_bytes)
            tmpfile.seek(0)
            self.font_path = tmpfile.name

    def get_clips(self, size_rate: float = 1.0) -> List[TextClip]:
        clips = []
        for caption_segment in self.captions.segments:
            clip = self.__make_word_clip(caption_segment, size_rate)
            clips.append(clip)
        return clips

    def __make_word_clip(self, caption_segment: CaptionSegment, size_rate: float) -> TextClip:
        start_time, end_time = [caption_segment.start, caption_segment.end]
        text = caption_segment.text
        show_text = text.upper() if self.config.upper_text else text
        show_text = show_text.replace(",", "").replace(".", "").strip()
        
        font_size = int(round(self.config.font_size * size_rate))
        stroke_width = int(round(self.config.stroke_width * size_rate))
        margin = int(round(self.config.marging * size_rate))
        
        word_clip = TextClip(
            text=show_text,
            font=self.font_path,
            font_size=font_size,
            color=self.config.color,
            stroke_color=self.config.stroke_color,
            stroke_width=stroke_width,
            text_align="center",
            margin=(margin, margin),
        ).with_start(start_time)
        word_clip = word_clip.with_duration(end_time - start_time)

        if self.config.fade_duration > 0:
            word_clip = CrossFadeIn(duration=self.config.fade_duration).apply(word_clip)
            word_clip = CrossFadeOut(duration=self.config.fade_duration).apply(
                word_clip
            )
        word_clip: TextClip = word_clip.with_position(
            ["center", self.config.vertical_position], relative=True
        )
        return word_clip
