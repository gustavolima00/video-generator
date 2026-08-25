from typing import List, Literal, Union
from pydantic import Field
from src.entities.base_yaml_model import BaseYAMLModel


class BackgroundCacheConfig(BaseYAMLModel):
    enabled: bool = Field(
        True,
        title=(
            "Serve background clips from a local directory, going to YouTube only "
            "when a clip is missing. Turning it off restores the previous behaviour: "
            "every run re-downloads every clip."
        ),
    )
    dir: str = Field(
        "~/.cache/video-generator/backgrounds",
        title=(
            "Where the cached mp4 files live. '~' is expanded and the directory is "
            "created on demand; the default sits outside the working tree so the "
            "clips are never picked up by git or a rebuild."
        ),
    )
    max_gigabytes: float = Field(
        20.0,
        gt=0,
        title=(
            "Disk budget for the cache directory, in gigabytes. When a download "
            "pushes the directory past it, the least recently used clips are "
            "removed until it fits again; serving a clip from the cache counts "
            "as using it."
        ),
    )


class PyTubeYouTubeConfig(BaseYAMLModel):
    type: Literal["pytube"] = "pytube"
    download_clients: List[str] = Field(
        default_factory=lambda: ["WEB", "MWEB", "WEB_SAFARI"],
        title=(
            "InnerTube clients to try, in order, when downloading a background. "
            "pytubefix's own default (ANDROID_VR) is rejected by YouTube's bot "
            "detection, so the client has to be named explicitly; the extras are "
            "fallbacks for when one of them starts getting blocked."
        ),
    )
    cache: BackgroundCacheConfig = Field(
        default_factory=BackgroundCacheConfig,
        title="Local cache for downloaded backgrounds",
    )


YouTubeConfigType = Union[PyTubeYouTubeConfig]
