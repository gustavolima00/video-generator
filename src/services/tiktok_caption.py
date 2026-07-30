"""Helpers for TikTok captions and hashtags."""

from __future__ import annotations

import re
from collections.abc import Iterable

import unidecode

DEFAULT_HASHTAGS = ("fyp", "storytime", "reddit")
MAX_HASHTAGS = 3

_TRAILING_HASHTAGS_RE = re.compile(r"(?:\s*#[^\s#]+)+\s*$")


def strip_trailing_hashtags(description: str) -> str:
    """Remove a trailing hashtag block from a caption/title."""
    return _TRAILING_HASHTAGS_RE.sub("", description or "").strip()


def normalize_hashtags(
    hashtags: Iterable[str] | None,
    *,
    defaults: Iterable[str] = DEFAULT_HASHTAGS,
    max_count: int = MAX_HASHTAGS,
) -> list[str]:
    """Return clean, deduped hashtags without leading '#'.

    LLMs sometimes return repeated tags, tags with '#' included, or a full
    copied hashtag block. Splitting and normalizing here keeps the final
    TikTok caption compact and prevents spammy duplicated blocks.

    The caller's tags come first and the generic defaults only fill the
    leftover slots: with a cap as small as ``MAX_HASHTAGS`` the defaults
    would otherwise consume every slot and the story-specific tags would
    never make it into the caption.
    """
    result: list[str] = []
    seen: set[str] = set()
    raw_tags = [*((hashtags or ())), *(defaults or ())]

    for raw_tag in raw_tags[: max_count * 4]:
        for tag in _split_hashtag_tokens(str(raw_tag)):
            clean = _clean_hashtag(tag)
            if not clean:
                continue
            key = _dedupe_key(clean)
            if key in seen:
                continue
            seen.add(key)
            result.append(clean)
            if len(result) >= max_count:
                return result

    return result


def _split_hashtag_tokens(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []

    tokens: list[str] = []
    for chunk in re.split(r"[\s,;]+", value):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "#" in chunk:
            tokens.extend(part for part in chunk.split("#") if part)
        else:
            tokens.append(chunk)
    return tokens


def _clean_hashtag(value: str) -> str:
    value = value.strip().lstrip("#")
    value = unidecode.unidecode(value)
    value = re.sub(r"[^0-9A-Za-z_]+", "", value)
    if not value or not re.search(r"[A-Za-z0-9]", value):
        return ""
    return value[:40]


def _dedupe_key(value: str) -> str:
    return unidecode.unidecode(value).casefold()
