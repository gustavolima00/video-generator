from typing import Literal, Union, Optional
from pydantic import Field
from src.entities.base_yaml_model import BaseYAMLModel


class LLMProviderConfig(BaseYAMLModel):
    provider: Literal["openai", "google", "ollama", "openrouter"] = "ollama"
    model: str = "gemma3:12b"
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    reasoning_effort: Optional[Literal["none", "minimal", "low", "medium", "high"]] = (
        Field(
            None,
            title=(
                "Reasoning effort for reasoning-capable models (openrouter only). "
                "'none' disables reasoning where the model supports it. Reasoning "
                "tokens are billed as completion tokens, so keep this low for "
                "creative-writing tasks."
            ),
        )
    )
    base_url: Optional[str] = Field(None, exclude=True)
    api_key: Optional[str] = Field(None, exclude=True)


class DSPyLLMConfig(BaseYAMLModel):
    type: Literal["dspy"] = "dspy"
    provider_config: LLMProviderConfig = Field(default_factory=LLMProviderConfig)


class PromptLLMConfig(BaseYAMLModel):
    type: Literal["prompt"] = "prompt"
    provider_config: LLMProviderConfig = Field(default_factory=LLMProviderConfig)


class MockLLMConfig(BaseYAMLModel):
    type: Literal["mock"] = "mock"


LLMConfigType = Union[DSPyLLMConfig, PromptLLMConfig, MockLLMConfig]
