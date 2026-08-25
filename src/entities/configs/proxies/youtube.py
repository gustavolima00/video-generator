from typing import List, Literal, Optional, Union
from pydantic import ConfigDict, Field, field_validator
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
    po_token: Optional[str] = Field(
        None,
        title=(
            "Proof-of-origin token proving the download comes from a real browser "
            "session. It is a secret, so it comes from YOUTUBE_PO_TOKEN in the .env "
            "and never from the yaml; see docs/po-token.md. Without it pytubefix "
            "mints its own, which is what YouTube answers with 429."
        ),
    )
    visitor_data: Optional[str] = Field(
        None,
        title=(
            "The visitor id the po_token was issued for, from YOUTUBE_VISITOR_DATA. "
            "YouTube only accepts the token together with it, so the two are set "
            "and renewed as a pair."
        ),
    )

    # The factory assigns the pair after loading the yaml, so the blank-to-None
    # normalisation has to run on assignment too — otherwise an operator who
    # empties the .env values would send empty strings as a token.
    model_config = ConfigDict(validate_assignment=True)

    @field_validator("po_token", "visitor_data")
    @classmethod
    def _blank_is_unset(cls, value: Optional[str]) -> Optional[str]:
        return (value or "").strip() or None


YouTubeConfigType = Union[PyTubeYouTubeConfig]
