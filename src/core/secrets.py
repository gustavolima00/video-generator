from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import Optional


class Secrets(BaseSettings):
    # External API settings
    openai_api_key: Optional[str] = None
    youtube_api_key: Optional[str] = None
    elevenlabs_api_key: Optional[str] = None
    leonardo_api_key: Optional[str] = None
    runpod_api_key: Optional[str] = None
    legnext_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    reddit_client_id: Optional[str] = None
    reddit_client_secret: Optional[str] = None
    reddit_user_agent: str = "video-generator/0.1"

    # LLM settings
    ollama_base_url: str = "http://localhost:11434"

    # Telegram bot tokens
    telegram_image_story_bot_token: Optional[str] = None
    telegram_satisfying_bot_token: Optional[str] = None

    # TikTok auto-publisher credentials. Non-secret tunables (model,
    # cookies path, headless, ...) live in config.yaml under
    # proxies.tiktok_publisher_config.
    tiktok_email: Optional[str] = None
    tiktok_password: Optional[str] = None

    # YouTube proof-of-origin token. Captured by hand from a browser session
    # and renewed the same way when it expires; see docs/po-token.md. Both
    # halves belong together — pytubefix sends the visitor data alongside the
    # token. Leaving them unset keeps downloads exactly as they are today.
    youtube_po_token: Optional[str] = None
    youtube_visitor_data: Optional[str] = None

    model_config = ConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


secrets = Secrets()
