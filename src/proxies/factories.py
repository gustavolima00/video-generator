from src.entities.configs.proxies.image_generation import (
    ImageGenerationConfigType,
    LeonardoImageGenerationConfig,
    LeonardoV2ImageGenerationConfig,
    MidjourneyImageGenerationConfig,
    LocalImageGenerationConfig,
    RunPodImageGenerationConfig,
    MockImageGenerationConfig,
)
from src.proxies.interfaces import IImageGeneratorProxy, ITranscriptionProxy
from src.proxies.leonardo_proxy import LeonardoImageProxy
from src.proxies.leonardo_v2_proxy import LeonardoV2ImageProxy
from src.proxies.midjourney_proxy import MidjourneyImageProxy
from src.proxies.local_sdxl_proxy import LocalSDXLImageProxy
from src.proxies.runpod_comfyui_proxy import RunPodComfyUIProxy
from src.proxies.mock_image_proxy import MockImageGeneratorProxy

from src.entities.configs.proxies.transcription import (
    TranscriptionConfigType,
    LocalTranscriptionConfig,
    OpenAITranscriptionConfig,
)
from src.proxies.local_whisper_proxy import LocalWhisperProxy
from src.proxies.openai_whisper_proxy import OpenAIWhisperProxy
from src.entities.configs.proxies.speech import (
    SpeechConfigType,
    EdgeTTSSpeechConfig,
    ElevenLabsSpeechConfig,
)
from src.proxies.edge_tts_proxy import EdgeTTSSpeechProxy
from src.proxies.elevenlabs_proxy import ElevenLabsSpeechProxy
from src.proxies.interfaces import (
    IImageGeneratorProxy,
    ITranscriptionProxy,
    ISpeechProxy,
    IRedditProxy,
)
from src.entities.configs.proxies.reddit import RedditConfigType, BS4RedditConfig, JsonRedditConfig
from src.proxies.reddit_proxy import BS4RedditProxy
from src.proxies.json_reddit_proxy import JsonRedditProxy
from src.entities.configs.proxies.llm import (
    LLMConfigType,
    PromptLLMConfig,
    DSPyLLMConfig,
    MockLLMConfig,
)
from src.proxies.llm_prompt_proxy import PromptLLMProxy
from src.proxies.llm_dspy_proxy import DSPyLLMProxy
from src.proxies.mock_llm_proxy import MockLLMProxy
from src.proxies.interfaces import ILLMProxy, IYouTubeProxy
from src.entities.configs.proxies.youtube import YouTubeConfigType, PyTubeYouTubeConfig
from src.proxies.pytube_proxy import PyTubeProxy
from src.proxies.caching_youtube_proxy import CachingYouTubeProxy

from src.proxies.interfaces import ICoverProxy
from src.proxies.playwright_cover_proxy import PlaywrightCoverProxy
from src.entities.configs.proxies.cover import CoverConfigType, PlaywrightCoverConfig

from src.proxies.interfaces import IVideoGeneratorProxy
from src.proxies.comfyui_video_proxy import ComfyUIVideoProxy
from src.entities.configs.proxies.video_generation import (
    VideoGenerationConfigType,
    ComfyUIVideoGenerationConfig,
)


class ImageGeneratorFactory:
    @staticmethod
    def create_optional(
        config: ImageGenerationConfigType | None,
        leonardo_api_key: str = None,
        runpod_api_key: str = None,
        legnext_api_key: str = None,
    ) -> IImageGeneratorProxy | None:
        if config is None:
            return None
        return ImageGeneratorFactory.create(
            config, leonardo_api_key, runpod_api_key, legnext_api_key,
        )

    @staticmethod
    def create(
        config: ImageGenerationConfigType,
        leonardo_api_key: str = None,
        runpod_api_key: str = None,
        legnext_api_key: str = None,
    ) -> IImageGeneratorProxy:
        if isinstance(config, LeonardoImageGenerationConfig):
            config.api_key = leonardo_api_key
            return LeonardoImageProxy(config=config)
        elif isinstance(config, LeonardoV2ImageGenerationConfig):
            config.api_key = leonardo_api_key
            return LeonardoV2ImageProxy(config=config)
        elif isinstance(config, MidjourneyImageGenerationConfig):
            config.api_key = legnext_api_key
            return MidjourneyImageProxy(config=config)
        elif isinstance(config, RunPodImageGenerationConfig):
            config.api_key = runpod_api_key
            return RunPodComfyUIProxy(config=config)
        elif isinstance(config, LocalImageGenerationConfig):
            return LocalSDXLImageProxy(config=config)
        elif isinstance(config, MockImageGenerationConfig):
            return MockImageGeneratorProxy()
        else:
            raise ValueError(f"Unknown Image Generation Configuration: {type(config)}")


class TranscriptionProxyFactory:
    @staticmethod
    def create(
        config: TranscriptionConfigType, openai_api_key: str = None
    ) -> ITranscriptionProxy:
        if isinstance(config, LocalTranscriptionConfig):
            return LocalWhisperProxy(config=config)
        elif isinstance(config, OpenAITranscriptionConfig):
            config.api_key = openai_api_key
            return OpenAIWhisperProxy(config=config)


class SpeechProxyFactory:
    @staticmethod
    def create(
        config: SpeechConfigType, elevenlabs_api_key: str = None
    ) -> ISpeechProxy:
        if isinstance(config, EdgeTTSSpeechConfig):
            return EdgeTTSSpeechProxy(config=config)
        elif isinstance(config, ElevenLabsSpeechConfig):
            config.api_key = elevenlabs_api_key
            return ElevenLabsSpeechProxy(config=config)
        else:
            raise ValueError(f"Unknown Speech Configuration: {type(config)}")


class RedditProxyFactory:
    @staticmethod
    def create(
        config: RedditConfigType,
        reddit_client_id: str = None,
        reddit_client_secret: str = None,
        reddit_user_agent: str = None,
    ) -> IRedditProxy:
        if isinstance(config, BS4RedditConfig):
            return BS4RedditProxy(config=config)
        elif isinstance(config, JsonRedditConfig):
            return JsonRedditProxy(
                config=config,
                client_id=reddit_client_id,
                client_secret=reddit_client_secret,
                user_agent=reddit_user_agent,
            )
        else:
            raise ValueError(f"Unknown Reddit Configuration: {type(config)}")


class LLMProxyFactory:
    @staticmethod
    def create_optional(
        config: LLMConfigType | None,
        openai_api_key: str = None,
        ollama_base_url: str = None,
        google_api_key: str = None,
        openrouter_api_key: str = None,
    ) -> ILLMProxy | None:
        if config is None:
            return None
        return LLMProxyFactory.create(
            config, openai_api_key, ollama_base_url, google_api_key, openrouter_api_key,
        )

    @staticmethod
    def create(
        config: LLMConfigType,
        openai_api_key: str = None,
        ollama_base_url: str = None,
        google_api_key: str = None,
        openrouter_api_key: str = None,
    ) -> ILLMProxy:
        if isinstance(config, MockLLMConfig):
            return MockLLMProxy()

        if config.provider_config.provider == "openai":
            config.provider_config.api_key = openai_api_key
        elif config.provider_config.provider == "ollama":
            config.provider_config.base_url = ollama_base_url
        elif config.provider_config.provider == "google":
            config.provider_config.api_key = google_api_key
        elif config.provider_config.provider == "openrouter":
            config.provider_config.api_key = openrouter_api_key

        if isinstance(config, DSPyLLMConfig):
            return DSPyLLMProxy(config=config)
        elif isinstance(config, PromptLLMConfig):
            return PromptLLMProxy(config=config)
        else:
            raise ValueError(f"Unknown LLM Configuration: {type(config)}")


class YouTubeProxyFactory:
    @staticmethod
    def create(config: YouTubeConfigType, youtube_api_key: str = None) -> IYouTubeProxy:
        if isinstance(config, PyTubeYouTubeConfig):
            proxy = PyTubeProxy(config=config)
            if config.cache.enabled:
                # Wrapping here keeps the cache invisible to the services: they
                # keep depending on IYouTubeProxy and never learn it exists.
                return CachingYouTubeProxy(inner=proxy, cache=config.cache)
            return proxy
        else:
            raise ValueError(f"Unknown YouTube Configuration: {type(config)}")


class VideoGeneratorFactory:
    @staticmethod
    def create(config: VideoGenerationConfigType) -> IVideoGeneratorProxy:
        if isinstance(config, ComfyUIVideoGenerationConfig):
            return ComfyUIVideoProxy(config=config)
        else:
            raise ValueError(f"Unknown Video Generation Configuration: {type(config)}")


class CoverProxyFactory:
    @staticmethod
    def create(config: CoverConfigType) -> ICoverProxy:
        if isinstance(config, PlaywrightCoverConfig):
            return PlaywrightCoverProxy(title_font_size=config.title_font_size)
        else:
            raise ValueError(f"Unknown Cover Configuration: {type(config)}")

