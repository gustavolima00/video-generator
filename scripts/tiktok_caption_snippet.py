"""Print a browser-console snippet that sets a TikTok caption.

The snippet runs the EXACT code the publisher agent uses
(``src/proxies/js/tiktok_caption.js``), so whatever it does in your console
is what the bot does on the server — handy for checking the caption/hashtag
behaviour against the live TikTok Studio page without a full publish run.

Usage:
    uv run python scripts/tiktok_caption_snippet.py "Minha legenda #fyp #storytime #reddit"
    uv run python scripts/tiktok_caption_snippet.py "Minha legenda" | pbcopy

Then, in Chrome:
    1. open https://www.tiktok.com/tiktokstudio/upload?from=upload
    2. upload a video and wait for it to finish processing (the caption box
       gets prefilled with the file name — that is the bug this checks)
    3. open DevTools (Cmd+Option+J) -> Console
    4. paste the snippet, press Enter, and read the report it prints
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.proxies.tiktok_publisher_tools import _CAPTION_JS  # noqa: E402
from src.proxies.tiktok_publisher_proxy import (  # noqa: E402
    BrowserUseTikTokPublisherProxy,
)

CAPTION_SELECTOR = "div[contenteditable='true'][role='combobox']"

REPORT_JS = """
/* ---- TikTok caption test ------------------------------------------
   Sets the caption exactly the way the publisher bot does.
   Expected: the prefilled file name is gone and the caption is exact.
   -------------------------------------------------------------------- */
(async () => {
  const EXPECTED = __EXPECTED__;
  const result = await __CAPTION_JS__;
  const norm = (s) => (s || '').split(/\\s+/).join(' ').trim();
  const ok = result.ok && norm(result.content) === norm(EXPECTED);
  console.log('%c' + (ok ? 'PASS' : 'FAIL'),
    'font-weight:bold;color:' + (ok ? 'green' : 'red') + ';font-size:14px');
  console.log('prefill found :', JSON.stringify(result.prefill));
  console.log('prefill cleared:', result.cleared, '(' + result.clearMethod + ')');
  console.log('caption now   :', JSON.stringify(result.content));
  console.log('caption wanted:', JSON.stringify(EXPECTED));
  console.log('hashtags highlighted:',
    result.hashtagsHighlighted + '/' + result.hashtagsWanted, result.highlighted);
  return result;
})()
"""


def build_snippet(caption: str, selector: str = CAPTION_SELECTOR) -> str:
    caption_js = _CAPTION_JS.replace("__SELECTOR__", json.dumps(selector)).replace(
        "__TEXT__", json.dumps(caption)
    )
    return (
        REPORT_JS.replace("__EXPECTED__", json.dumps(caption))
        .replace("__CAPTION_JS__", caption_js.strip())
        .strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "description",
        help="Caption text. Hashtags may be included, or use --hashtag.",
    )
    parser.add_argument(
        "--hashtag",
        action="append",
        default=None,
        help="Hashtag to append (repeatable). Normalised exactly like a real "
        "publish: caller tags first, defaults fill the rest, capped at 3.",
    )
    parser.add_argument(
        "--selector",
        default=CAPTION_SELECTOR,
        help="CSS selector of the caption field (default: TikTok Studio's).",
    )
    args = parser.parse_args()

    caption = BrowserUseTikTokPublisherProxy._format_description(
        args.description, args.hashtag
    )
    print(build_snippet(caption, args.selector))


if __name__ == "__main__":
    main()
