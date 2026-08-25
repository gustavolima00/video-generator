"""T014 — Milestone 2 live gate (parallel-path parity + multilingual).

Runs a fixed sample of posts through the parallel generation paths that M2
targets — the single-part prompt path (`story.jinja2` / PromptLLMProxy) and the
DSPy paths (single + two-part) — in two target languages (pt-br + en), and
checks the strong-hook anatomy holds consistently across all of them:

- `title` present (cover hook), non-empty, no forbidden words;
- narration (`script`/`part1`/`part2`) does NOT start with the title;
- narration does NOT open with a spoken "Part N."/"Parte N." marker.

Writes results-m2.json for evidence. Requires OPENROUTER_API_KEY in .env.
"""

import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv

from src.entities.configs.proxies.llm import (
    DSPyLLMConfig,
    LLMProviderConfig,
    PromptLLMConfig,
)
from src.entities.language import Language
from src.proxies.factories import LLMProxyFactory
from tests.services.forbidden_words import FORBIDDEN_WORDS

MODEL = "moonshotai/kimi-k2.6"
PART_MARKERS = ("Parte 1.", "Parte 2.", "Part 1.", "Part 2.")

# Fixed sample (2 posts) — self-contained so the gate does not depend on Reddit.
POSTS = [
    {
        "title": "My neighbor kept parking in my reserved spot so I had his car towed every time",
        "text": (
            "I rent a parking spot in my building and pay extra for it. A new neighbor moved in "
            "and started parking in my spot every single day. I left notes, I asked him politely, "
            "I even talked to the building manager. He laughed and said 'what are you going to do "
            "about it'. So I started calling the tow company every time his car was in my spot. "
            "The first week his car got towed four times. He had to pay the fee each time. He was "
            "furious and confronted me in the lobby, but the manager backed me up because the spot "
            "is legally mine. After the fifth tow he finally stopped and started parking on the street."
        ),
    },
    {
        "title": "My boss cut me from the CEO presentation to take credit for my 3-month project",
        "text": (
            "I (29f) spent three months building a client dashboard the directors were excited about. "
            "The week of the big presentation to the CEO, my manager M(48m) removed me from the meeting "
            "invite and said he would 'represent the team'. On presentation day the CEO asked M to click "
            "into a specific feature I had built. M had no idea how it worked and froze. I was watching "
            "from my desk. The CEO asked who actually built it, a coworker said my name, and the CEO "
            "called me in to walk through it myself. Two weeks later I was moved to report directly to "
            "the director, and M's role was quietly reduced."
        ),
    },
]

LANGUAGES = [("pt-br", Language.PORTUGUESE), ("en", Language.ENGLISH)]


def _find_forbidden(text):
    lowered = (text or "").lower()
    return [w for w in FORBIDDEN_WORDS if re.search(r"\b" + re.escape(w.lower()) + r"\b", lowered)]


def _check_narration(title, narration):
    text = (narration or "").strip()
    head = text[:40]
    return {
        "not_start_title": bool(text) and not text.startswith((title or "").strip()),
        "no_marker_head": not any(m in head for m in PART_MARKERS),
    }


def _record(path, lang, title, gender, narrations):
    checks = {
        "title_present": bool((title or "").strip()),
        "title_no_forbidden": not _find_forbidden(title),
    }
    for name, narration in narrations.items():
        c = _check_narration(title, narration)
        checks[f"{name}_not_start_title"] = c["not_start_title"]
        checks[f"{name}_no_marker_head"] = c["no_marker_head"]
        checks[f"{name}_no_forbidden"] = not _find_forbidden(narration)
    rec = {
        "path": path,
        "lang": lang,
        "title": title,
        "narrator_gender": gender,
        "checks": checks,
        "all_pass": all(checks.values()),
        "heads": {k: (v or "").strip()[:70] for k, v in narrations.items()},
    }
    return rec


async def main():
    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY not set")

    def provider():
        return LLMProviderConfig(
            provider="openrouter", model=MODEL, temperature=0.7, api_key=api_key
        )

    prompt_proxy = LLMProxyFactory.create(PromptLLMConfig(provider_config=provider()))
    dspy_proxy = LLMProxyFactory.create(DSPyLLMConfig(provider_config=provider()))

    results = []
    errors = []
    for post in POSTS:
        for lang_label, lang in LANGUAGES:
            # 1) single-part prompt path (story.jinja2)
            try:
                r = await prompt_proxy.generate_story(post["title"], post["text"], lang)
                results.append(
                    _record("prompt_single", lang_label, r["title"], r["narrator_gender"],
                            {"script": r["script"]})
                )
            except Exception as e:
                errors.append({"path": "prompt_single", "lang": lang_label, "error": str(e)})

            # 2) DSPy single-part path
            try:
                r = await dspy_proxy.generate_story(post["title"], post["text"], lang)
                results.append(
                    _record("dspy_single", lang_label, r["title"], r["narrator_gender"],
                            {"script": r["script"]})
                )
            except Exception as e:
                errors.append({"path": "dspy_single", "lang": lang_label, "error": str(e)})

            # 3) DSPy two-part path (few-shot loaded from two_part_story.yaml)
            try:
                r = await dspy_proxy.generate_two_part_story(post["title"], post["text"], lang)
                results.append(
                    _record("dspy_two_part", lang_label, r["title"], r["narrator_gender"],
                            {"part1": r["part1"], "part2": r["part2"]})
                )
            except Exception as e:
                errors.append({"path": "dspy_two_part", "lang": lang_label, "error": str(e)})

    summary = {
        "model": MODEL,
        "total": len(results),
        "all_checks_pass": all(r["all_pass"] for r in results) and not errors,
        "by_path": sorted({r["path"] for r in results}),
        "by_lang": sorted({r["lang"] for r in results}),
        "results": results,
        "errors": errors,
    }
    out = os.path.join(os.path.dirname(__file__), "results-m2.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"model={MODEL} paths={summary['by_path']} langs={summary['by_lang']}")
    print(f"records={summary['total']} errors={len(errors)} all_checks_pass={summary['all_checks_pass']}")
    for r in results:
        print(f"  [{r['path']:>13} {r['lang']}] all_pass={r['all_pass']} title={r['title']!r}")
        for k, v in r["heads"].items():
            print(f"        {k}: {v}")
    for e in errors:
        print(f"  ERROR [{e['path']} {e['lang']}]: {e['error']}")


if __name__ == "__main__":
    asyncio.run(main())
