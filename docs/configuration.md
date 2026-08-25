# Configuration Guide

All configuration is split into two layers:

1. **`config.yaml`** — Behavioral settings for proxies and services (providers, models, video dimensions, etc.)
2. **`.env`** — Secret API keys and sensitive values

Any key omitted from `config.yaml` will use the default value defined in the Pydantic model.

---

## Config Structure

```yaml
proxies:
  transcription_config: { ... }
  image_generation_config: { ... }
  speech_config: { ... }
  reddit_config: { ... }
  llm_config: { ... }
  youtube_config: { ... }
  cover_config: { ... }

services:
  video_config: { ... }
  captions_config: { ... }
```

---

## Proxies

### LLM (`llm_config`)

Controls the language model used for story generation, translation, and transcription enhancement.

**Approach** — choose between `dspy` (structured signatures with few-shot examples) or `prompt` (raw prompt-based):

```yaml
llm_config:
  type: dspy          # or "prompt"
  provider_config:
    provider: ollama   # "ollama" | "openai" | "google"
    model: gemma3:12b
    temperature: 0.7
    max_tokens: 2000
```

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `dspy` / `prompt` | `dspy` | LLM approach: DSPy signatures with few-shot learning or raw prompt templates |
| `provider_config.provider` | `ollama` / `openai` / `google` | `ollama` | Which LLM provider to use |
| `provider_config.model` | `str` | `gemma3:12b` | Model identifier |
| `provider_config.temperature` | `float` | `0.7` | Sampling temperature |
| `provider_config.max_tokens` | `int` | `2000` | Maximum output tokens |

> **Secrets**: `openai_api_key` (for OpenAI provider), `ollama_base_url` (for Ollama, defaults to `http://localhost:11434`)

---

### Speech (`speech_config`)

Text-to-speech generation.

```yaml
speech_config:
  type: edge-tts     # or "elevenlabs"
  voices:            # optional per-language voice overrides
    pt:
      male_voice_id: "pt-BR-AntonioNeural"
      female_voice_id: "pt-BR-FranciscaNeural"
```

| Provider | `type` value | Requires API Key | Notes |
|---|---|---|---|
| Edge TTS | `edge-tts` | No | Free, Microsoft Edge neural voices |
| ElevenLabs | `elevenlabs` | Yes (`elevenlabs_api_key`) | Premium voice cloning |

Both support a `voices` dictionary mapping `Language` enum values to gender-specific voice IDs.

---

### Transcription (`transcription_config`)

Speech-to-text with word-level timestamps.

```yaml
transcription_config:
  type: local         # or "openai"
  model: base         # whisper model size
```

| Provider | `type` | Requires API Key | Notes |
|---|---|---|---|
| Local Whisper | `local` | No | Runs locally. Models: `tiny`, `base`, `small`, `medium`, `large` |
| OpenAI Whisper | `openai` | Yes (`openai_api_key`) | Cloud API, model: `whisper-1` |

---

### Image Generation (`image_generation_config`)

Generates images for the video pipeline.

```yaml
image_generation_config:
  type: local          # or "leonardo"
  model_id: stabilityai/sdxl-turbo
```

| Provider | `type` value | Requires API Key | Notes |
|---|---|---|---|
| Local SDXL | `local` | No | HuggingFace model, runs on your GPU |
| Leonardo AI | `leonardo` | Yes (`leonardo_api_key`) | Cloud API |

---

### Cover (`cover_config`)

Reddit-style cover image generation using Playwright.

```yaml
cover_config:
  title_font_size: 150
```

| Field | Type | Default | Description |
|---|---|---|---|
| `title_font_size` | `int` | `150` | Font size for the Reddit post title on the cover |

Currently only `playwright` provider is available.

---

### Reddit (`reddit_config`)

Reddit post scraping.

```yaml
reddit_config:
  type: bs4
```

Currently only `bs4` (BeautifulSoup4 scraping) is available. No API key required.

---

### YouTube (`youtube_config`)

YouTube video listing and downloading for background compilations.

```yaml
youtube_config:
  type: pytube
  download_clients: [WEB, MWEB, WEB_SAFARI]
  cache:
    enabled: true
    dir: ~/.cache/video-generator/backgrounds
    max_gigabytes: 20
```

Currently only `pytube` is available. No API key required for basic usage.
Downloads can optionally carry a proof-of-origin token to get past YouTube's bot
detection — it is a secret, so it lives in the `.env` rather than here; see
[po-token.md](./po-token.md).

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `"pytube"` | `"pytube"` | Backend used to list and download videos |
| `download_clients` | `list[str]` | `["WEB", "MWEB", "WEB_SAFARI"]` | InnerTube clients tried in order. pytubefix's own default (`ANDROID_VR`) is answered with a bot-detection error, so the client is always named explicitly; the extras are fallbacks for when one starts getting blocked |

#### Background cache (`youtube_config.cache`)

Downloaded clips are kept on disk and reused. The daily run draws from the same
pool of videos, so re-downloading them is both the slowest step and what gets
the machine's IP throttled (HTTP 429) — with a warm cache a repeated run makes
no download requests at all and completes even while YouTube is refusing them.

Omitting the whole `cache` block keeps the defaults below (the cache is on).

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `true` | `false` restores the previous behaviour: every run re-downloads every clip |
| `dir` | `str` | `~/.cache/video-generator/backgrounds` | Where the mp4 files live. `~` is expanded and the directory is created on demand; the default sits outside the working tree. Caches are per machine — nothing is shared between the laptop and the server |
| `max_gigabytes` | `float` | `20` | Disk budget for the directory (GiB). Must be greater than zero. After each download the least recently used clips are evicted until the directory fits again — serving a clip from the cache counts as using it, so the pool in daily rotation survives |

Entries are named `{video_id}-{hq|lq}.mp4`, so the two quality tracks are cached
independently and never serve each other. Caching is best-effort: an unreadable
or unwritable directory logs a warning and the run downloads as before, and a
truncated file is discarded and downloaded again rather than failing the run.
Download errors themselves — a 429 included — still abort immediately.

Eviction only ever touches `{video_id}-{hq|lq}.mp4` entries in that directory,
and never the clip the current run just downloaded — those bytes are already in
memory, so even a cap smaller than a single clip degrades to "nothing is cached"
rather than breaking the run.

---

## Services

### Video (`video_config`)

Controls final video composition.

```yaml
video_config:
  width: 1080
  height: 1920
  padding: 60
  cover_duration: 5
  end_silece_seconds: 3
  youtube_channel_url: "https://www.youtube.com/channel/..."
  youtube_channel_urls:
    - "https://www.youtube.com/@channel-one"
    - "https://www.youtube.com/@channel-two"
  youtube_channel_strategy: "all"
  youtube_pool_size: 100
  watermark_path: null
  ffmpeg_params: []
```

| Field | Type | Default | Description |
|---|---|---|---|
| `width` | `int` | `1080` | Output video width (px) |
| `height` | `int` | `1920` | Output video height (px), 9:16 for TikTok |
| `padding` | `int` | `60` | Horizontal padding for overlays (cover, watermark) |
| `cover_duration` | `int` | `5` | Seconds the cover image is shown at the start |
| `end_silece_seconds` | `int` | `3` | Silent padding appended to the end of the audio |
| `youtube_channel_url` | `str` | *(see default)* | Fallback YouTube channel for background video compilation |
| `youtube_channel_urls` | `list[str]` | `[]` | YouTube channels available for background video compilation. When populated, this takes precedence over `youtube_channel_url` |
| `youtube_channel_strategy` | `"random"` or `"all"` | `"random"` | Whether each compilation uses one random configured channel or merges candidates from all configured channels |
| `youtube_pool_size` | `int` | `50` | Newest videos/shorts to consider per selected channel. `0` means all returned IDs |
| `watermark_path` | `str?` | `null` | Path to a watermark image file (loaded at startup) |
| `ffmpeg_params` | `list[str]` | `[]` | Extra ffmpeg parameters for video encoding |

> When `--low-quality` is used, `width`, `height`, and `padding` are proportionally scaled down to a 400px height target.

---

### Captions (`captions_config`)

Controls subtitle/caption rendering.

```yaml
captions_config:
  font_path: default_font.ttf
  font_size: 110
  color: "#FFFFFF"
  stroke_color: "#000000"
  stroke_width: 8
  upper_text: false
  marging: 50
  fade_duration: 0
```

| Field | Type | Default | Description |
|---|---|---|---|
| `font_path` | `str` | `default_font.ttf` | Path to the TTF font file |
| `font_size` | `int` | `110` | Base font size (auto-scaled in low quality) |
| `color` | `str` | `#FFFFFF` | Text color (hex) |
| `stroke_color` | `str` | `#000000` | Text outline color |
| `stroke_width` | `int` | `8` | Outline thickness (auto-scaled in low quality) |
| `upper_text` | `bool` | `false` | Force uppercase captions |
| `marging` | `int` | `50` | Text margin in pixels |
| `fade_duration` | `float` | `0` | Fade in/out duration for each word (seconds) |

---

## Secrets (`.env`)

API keys and sensitive configuration live in a `.env` file at the project root.

```env
OPENAI_API_KEY=sk-...
YOUTUBE_API_KEY=AIza...
ELEVENLABS_API_KEY=...
LEONARDO_API_KEY=...
OLLAMA_BASE_URL=http://localhost:11434
```

| Variable | Required For | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI transcription / LLM | — |
| `YOUTUBE_API_KEY` | YouTube API access | — |
| `ELEVENLABS_API_KEY` | ElevenLabs speech | — |
| `LEONARDO_API_KEY` | Leonardo AI image generation | — |
| `OLLAMA_BASE_URL` | Ollama LLM provider | `http://localhost:11434` |
| `YOUTUBE_PO_TOKEN` | Background downloads rejected by YouTube's bot detection — see [po-token.md](./po-token.md) | — |
| `YOUTUBE_VISITOR_DATA` | Set together with `YOUTUBE_PO_TOKEN`; YouTube only accepts the token alongside the visitor id it was issued for | — |

All secrets are optional — only provide the ones needed by your chosen providers.
