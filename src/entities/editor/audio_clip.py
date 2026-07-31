import tempfile
from moviepy import (
    AudioFileClip,
    CompositeAudioClip,
    AudioArrayClip,
    concatenate_audioclips,
    AudioClip as MoviepyAudioClip,
)
import numpy as np


class AudioClip:
    clip: MoviepyAudioClip

    def __init__(self, file_path: str = None, volume=1, bytes: bytes = None):
        self.file_path = file_path
        if bytes:
            # Create a temporary file with the audio bytes
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmpfile:
                tmpfile.write(bytes)
                tmpfile.flush()
                self.clip = AudioFileClip(tmpfile.name)
        else:
            self.clip = AudioFileClip(file_path)
        self.clip = self.clip.with_volume_scaled(volume)

    def add_end_silence(self, duration_in_seconds):
        silence = self._silence(duration_in_seconds)
        if silence is None:
            return
        self.clip = concatenate_audioclips([self.clip, silence])

    def add_start_silence(self, duration_in_seconds):
        """Push the narration back by *duration_in_seconds* of silence.

        Used so the story waits for the cover instead of playing under it.
        """
        silence = self._silence(duration_in_seconds)
        if silence is None:
            return
        self.clip = concatenate_audioclips([silence, self.clip])

    @staticmethod
    def _silence(duration_in_seconds) -> AudioArrayClip | None:
        frames = int(round(44100 * float(duration_in_seconds)))
        if frames <= 0:
            return None
        return AudioArrayClip(np.zeros((frames, 2)), fps=44100)

    def ajust_duration(self, duration):
        if duration > self.clip.duration:
            repeats = int(-(-duration // self.clip.duration))
            clips = [self.clip] * repeats
            self.clip = concatenate_audioclips(clips)[0:duration]
        elif duration < self.clip.duration:
            self.clip = self.clip[0:duration]

    def merge(self, other_clip: "AudioClip"):
        other_clip.ajust_duration(self.clip.duration)
        self.clip = CompositeAudioClip([self.clip, other_clip.clip])
