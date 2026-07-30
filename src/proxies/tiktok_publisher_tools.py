"""Custom browser-use tools for the TikTok publisher agent.

We deliberately keep the surface tiny:

* ``run_js(code)``                — execute arbitrary JS, return the value
* ``click_by_text(text, role?)``  — find a visible element by its text and click
* ``set_contenteditable(selector, text)`` — properly clear+type a DraftJS
                                            (or other rich-text) field
* ``get_text(selector)``          — read the visible text of an element

The philosophy is "few sharp tools, big toolbox via JS" rather than a
zoo of pre-defined high-level actions. The agent reaches for ``run_js``
when the standard browser-use actions can't do the job — e.g. clearing
DraftJS contenteditable, reading internal state, finding elements by
fuzzy text matching that the DOM dump indices don't expose.

The other three (``click_by_text`` / ``set_contenteditable`` /
``get_text``) are convenience wrappers that bake reliable JS snippets
into named actions so the agent doesn't have to retype them every run.

All four actions are best-effort: on JS error we return a structured
error string in ``ActionResult.extracted_content`` so the agent can
read it, retry differently, or call ``done(success=False)``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from browser_use import ActionResult
from browser_use.tools.service import Tools
from pydantic import BaseModel, Field

from src.core.logging_config import get_logger

_logger = get_logger(__name__)


def _wrap_js_for_eval(code: str) -> str:
    """Make user-supplied JS legal as a CDP ``Runtime.evaluate`` expression.

    CDP evaluates the input as an EXPRESSION, so a top-level ``return``
    is a SyntaxError. The agent (correctly, intuitively) writes things
    like ``return document.title`` because that's what every JS console
    accepts. We auto-wrap such snippets in an IIFE so both styles work:

    * ``return X``                   -> wrapped in ``(() => { ... })()``
    * ``X``  (bare expression)       -> passed through unchanged
    * ``(() => ...)()``  (already IIFE) -> passed through unchanged
    * ``async () => await X``        -> passed through (caller adds ``()``)
    """
    stripped = code.strip()
    if not stripped:
        return code
    # Already an IIFE call, an arrow function, or a parenthesized expression.
    if stripped.startswith("("):
        return code
    # Top-level return statement => wrap in an IIFE so the bare return
    # is legal. We use a non-async arrow; await usage inside still works
    # because awaitPromise=True on Runtime.evaluate handles top-level
    # promises returned from the IIFE.
    if re.search(r"^\s*return\b|\n\s*return\b", code):
        return f"(() => {{ {code} }})()"
    # Multi-statement code without an explicit return. Wrap so it's at
    # least syntactically valid; result will be undefined unless the
    # last expression is naturally returned (which JS doesn't do here).
    if ";" in stripped.rstrip(";") or "\n" in stripped:
        return f"(() => {{ {code} }})()"
    return code


# ---------------------------------------------------------------------------
# Pydantic param models — these define the JSON shape the LLM must produce.
# ---------------------------------------------------------------------------


class RunJSAction(BaseModel):
    """Run a JavaScript snippet in the active tab."""

    code: str = Field(
        description=(
            "JavaScript to evaluate in the active tab. The snippet is run "
            "with Runtime.evaluate (returnByValue=true, awaitPromise=true). "
            "Use `return <expr>` for non-trivial values; bare expressions "
            "also work. Wrap in an IIFE for multi-statement code: "
            "`(() => { const x = ...; return x; })()`. "
            "On exception, the error message is returned to you as text."
        )
    )


class ClickByTextAction(BaseModel):
    """Click a visible element whose innerText matches."""

    text: str = Field(
        description=(
            "Visible text to match (case-insensitive substring). Matches "
            "are sorted smallest-first by bounding-box area so that inner "
            "elements are preferred over large ancestor wrappers."
        )
    )
    role: Optional[str] = Field(
        default=None,
        description=(
            "Optional ARIA role to narrow the search (e.g. 'button', "
            "'tab', 'link', 'menuitem'). If omitted, any element matches."
        ),
    )
    index: int = Field(
        default=1,
        description=(
            "Which match to click when multiple elements share the same "
            "text (sorted smallest-first by area). "
            "1 = smallest (default), 2 = second-smallest, etc."
        ),
    )


class SetContenteditableAction(BaseModel):
    """Clear and re-fill a contenteditable element (DraftJS-friendly)."""

    selector: str = Field(
        description=(
            "CSS selector for the contenteditable target. For TikTok's "
            "caption editor: \"div[contenteditable='true'][role='combobox']\""
        )
    )
    text: str = Field(description="Text to set as the entire field content.")


class GetTextAction(BaseModel):
    """Read the visible text of an element matching a CSS selector."""

    selector: str = Field(description="CSS selector. The first match is used.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _eval_js(browser_session, expression: str) -> dict:
    """Run a JS expression in the active tab. Returns a dict with either
    ``{"value": <serialized>}`` or ``{"error": <message>}``.

    Wraps ``Runtime.evaluate`` with the flags we always want:
    ``returnByValue=True`` so we get a serializable value back, and
    ``awaitPromise=True`` so async JS works.
    """
    cdp_session = await browser_session.get_or_create_cdp_session()
    try:
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
            session_id=cdp_session.session_id,
        )
    except Exception as exc:
        return {"error": f"CDP transport error: {type(exc).__name__}: {exc}"}

    exc_details = result.get("exceptionDetails")
    if exc_details:
        text = exc_details.get("text") or ""
        exc_obj = exc_details.get("exception") or {}
        desc = exc_obj.get("description") or exc_obj.get("value") or ""
        return {"error": f"{text}: {desc}".strip(": ")}

    res = result.get("result", {})
    if "value" in res:
        return {"value": res["value"]}
    # Some return types (e.g. undefined) come back without a "value" key.
    return {"value": None}


# ---------------------------------------------------------------------------
# Caption typing (DraftJS + hashtag suggestions)
# ---------------------------------------------------------------------------
#
# Written as a JS template rather than an f-string because it is mostly
# braces. ``__SELECTOR__`` and ``__TEXT__`` are substituted with JSON.
#
# Why it looks like this (all of it was measured against the live page):
#
# * ``execCommand('insertText')`` — what this tool used to do — is NOT
#   accepted by DraftJS. It writes to the DOM while ``editorState`` stays
#   empty, which both loses the text on submit and corrupts Draft badly
#   enough to trip TikTok's React error boundary.
# * A synthetic ``paste`` ClipboardEvent goes through Draft's first-class
#   onPaste path and IS accepted.
# * Pasting "#tag" alone does not make it a real hashtag. TikTok only
#   decorates the tag when it is chosen from the suggestion dropdown, so
#   each hashtag is inserted separately and then clicked in the list.
# * TikTok prefills the caption with the uploaded file's base name (e.g.
#   "story_1"). Draft's onPaste bails out on an EMPTY string, so pasting
#   '' over a select-all does not clear anything — the prefill survived
#   and our caption was appended to it. Clearing therefore has to happen
#   by pasting something non-empty OVER the selection: we drop a sentinel
#   char, confirm Draft took it, and leave it selected so the first real
#   chunk replaces it.
#
# The reliable signal that Draft accepted input is the placeholder
# disappearing — ``innerText`` can show text Draft never registered.
_CAPTION_JS = r"""
(async () => {
  const SEL = __SELECTOR__;
  const TEXT = __TEXT__;
  const PLACEHOLDER = '.public-DraftEditorPlaceholder-root';
  const MENUS = '[role="option"], [role="listbox"], [role="menu"]';
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const editor = () => document.querySelector(SEL);
  if (!editor()) return { ok: false, reason: 'selector did not match' };

  const placeholderVisible = () => {
    const ph = document.querySelector(PLACEHOLDER);
    return !!(ph && ph.offsetParent);
  };

  // Draft needs a real caret, not just focus, or the paste is dropped.
  function focusEnd() {
    const el = editor();
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function hasCaret() {
    const el = editor();
    const sel = window.getSelection();
    return !!(el && document.activeElement === el && sel && sel.rangeCount > 0
              && el.contains(sel.getRangeAt(0).startContainer));
  }

  function insertPaste(text) {
    const el = editor();
    if (!hasCaret()) focusEnd();
    const dt = new DataTransfer();
    dt.setData('text/plain', text);
    el.dispatchEvent(new ClipboardEvent('paste', {
      clipboardData: dt, bubbles: true, cancelable: true }));
  }

  function selectAll() {
    const el = editor();
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  const content = () => (editor().innerText || '').trim();

  // Wipe whatever TikTok prefilled (usually the file name). Draft drops
  // an empty paste, so we paste a sentinel over the full selection and
  // then re-select it: the caller's first real paste replaces it.
  // Fallback is Draft's own Backspace command via a synthetic keydown.
  async function clearAll() {
    const SENTINEL = '⁣'; // invisible separator: harmless if it survives
    selectAll();
    insertPaste(SENTINEL);
    await sleep(300);
    const after = content();
    if (after === SENTINEL || after === '') {
      // Leave the sentinel selected so the first real paste replaces it.
      selectAll();
      return { cleared: true, method: 'paste-replace' };
    }
    selectAll();
    editor().dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Backspace', code: 'Backspace', keyCode: 8, which: 8,
      bubbles: true, cancelable: true }));
    await sleep(300);
    const left = content();
    if (left === '') {
      focusEnd();
      return { cleared: true, method: 'keydown-backspace' };
    }
    focusEnd();
    return { cleared: false, method: 'none', left: left };
  }

  const menusNow = () =>
    Array.from(document.querySelectorAll(MENUS)).filter((el) => el.offsetParent);

  function clickIt(item) {
    item.scrollIntoView({ block: 'nearest' });
    for (const type of ['mouseover', 'mousedown', 'mouseup', 'click']) {
      item.dispatchEvent(new MouseEvent(type,
        { bubbles: true, cancelable: true, view: window }));
    }
  }

  // Paste "#tag", wait for the suggestion list, click the matching row.
  // Polls rather than sleeping a fixed time — the list is a network call.
  async function addHashtag(tag) {
    const before = new Set(menusNow());
    insertPaste('#' + tag);
    let fresh = [];
    const start = performance.now();
    while (performance.now() - start < 4000) {
      await sleep(250);
      fresh = menusNow().filter((n) => !before.has(n));
      if (fresh.length) break;
    }
    if (!fresh.length) {
      // Niche/new tags may have no suggestion at all. The plain text is
      // already in the caption; TikTok usually linkifies it on publish.
      return { tag: tag, picked: false, reason: 'no-dropdown' };
    }
    const want = tag.toLowerCase();
    const norm = (e) => (e.innerText || '').trim().toLowerCase()
      .replace(/^#/, '').split(/\s/)[0];
    const exact = fresh.find((e) => norm(e) === want);
    const loose = fresh.find((e) => (e.innerText || '').toLowerCase().includes(want));
    const item = exact || loose || fresh[0];
    clickIt(item);
    await sleep(700);
    return { tag: tag, picked: true, exact: !!exact };
  }

  const parts = TEXT.split(/(#[\p{L}\p{N}_]+)/u).filter((p) => p !== '');
  if (!parts.length) return { ok: false, reason: 'no caption text to set' };

  focusEnd();
  const prefill = content();
  const clear = await clearAll();
  await sleep(300);

  const wanted = parts.filter((p) => p.startsWith('#')).length;
  const results = [];
  for (const part of parts) {
    if (!editor()) return { ok: false, reason: 'editor disappeared mid-typing' };
    if (part.startsWith('#')) {
      results.push(await addHashtag(part.slice(1)));
    } else {
      insertPaste(part);
      await sleep(250);
    }
  }
  await sleep(600);

  const el = editor();
  if (!el) return { ok: false, reason: 'editor disappeared' };
  const spans = Array.from(el.querySelectorAll('span'))
    .filter((s) => (s.innerText || '').trim().startsWith('#'))
    .map((s) => (s.innerText || '').trim());
  return {
    ok: true,
    content: el.innerText,
    draftAccepted: !placeholderVisible(),
    prefill: prefill,
    cleared: clear.cleared,
    clearMethod: clear.method,
    hashtagsWanted: wanted,
    hashtagsHighlighted: spans.length,
    highlighted: spans,
    results: results,
  };
})()
"""


def _summarize_value(val: Any, limit: int = 600) -> str:
    """Render a JS return value as a short string for the agent."""
    try:
        s = json.dumps(val, ensure_ascii=False)
    except Exception:
        s = repr(val)
    return s if len(s) <= limit else s[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def build_tools() -> Tools:
    """Construct a Tools instance with TikTok-publisher-specific actions
    layered on top of browser-use's defaults.

    The default tools are kept (go_to_url, click_element_by_index,
    input_text, scroll, wait, done, ...). We just add four extra
    actions geared for tricky DOM cases.
    """
    tools = Tools()

    @tools.registry.action(
        (
            "Execute JavaScript in the active tab. Use this when the "
            "standard browser-use actions cannot do the job — e.g. "
            "clearing a DraftJS contenteditable, reading internal page "
            "state, matching elements by visible text, dispatching "
            "synthetic events, or interacting with custom widgets. "
            "Both styles work: a bare expression like `document.title`, "
            "or a snippet that uses `return X` (auto-wrapped in an IIFE "
            "for you). The return value is JSON-serialized and returned "
            "as text. On exception you get the error message back, "
            "annotated with a hint about the IIFE wrapping if relevant."
        ),
        param_model=RunJSAction,
    )
    async def run_js(params: RunJSAction, browser_session):
        wrapped = _wrap_js_for_eval(params.code)
        if wrapped != params.code:
            _logger.debug("run_js auto-wrapped code in IIFE")
        result = await _eval_js(browser_session, wrapped)
        if "error" in result:
            err = result["error"]
            hint = ""
            # Defensive: if for some reason the wrapping logic missed
            # a case, surface the IIFE hint to the agent so it can
            # recover next step.
            if "Illegal return" in err or "Unexpected token" in err:
                hint = (
                    " Hint: wrap your code in `(() => { ... })()` if you "
                    "want to use a top-level return statement."
                )
            msg = f"run_js error: {err}{hint}"
            _logger.warning(msg)
            return ActionResult(
                extracted_content=msg,
                long_term_memory=msg[:200],
                error=err,
            )
        text = _summarize_value(result["value"])
        msg = f"run_js -> {text}"
        _logger.info(msg)
        return ActionResult(extracted_content=msg, long_term_memory=msg[:200])

    @tools.registry.action(
        (
            "Click the first visible element whose innerText contains "
            "`text` (case-insensitive). Optionally narrow by ARIA "
            "`role` (e.g. 'button'). Useful when DOM-index click is "
            "unreliable because many elements share the same class. "
            "Returns the element's bounding box on success, or an error "
            "if nothing matched."
        ),
        param_model=ClickByTextAction,
    )
    async def click_by_text(params: ClickByTextAction, browser_session):
        text_json = json.dumps(params.text)
        idx = params.index
        role_clause = (
            f"el.getAttribute('role') === {json.dumps(params.role)}"
            if params.role
            else "true"
        )
        expr = f"""
        (() => {{
          const target = {text_json}.toLowerCase();
          const idx = {idx};
          const all = Array.from(document.querySelectorAll('*'));
          const matches = [];
          for (const el of all) {{
            if (!({role_clause})) continue;
            if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
            const t = (el.innerText || el.textContent || '').toLowerCase();
            if (t && t.includes(target)) {{
              const r = el.getBoundingClientRect();
              if (r.width === 0 || r.height === 0) continue;
              matches.push({{ el, area: r.width * r.height }});
            }}
          }}
          if (matches.length === 0) return {{ ok: false, reason: 'no visible element matched' }};
          matches.sort((a, b) => a.area - b.area);
          const pick = idx - 1;
          if (pick < 0 || pick >= matches.length) return {{ ok: false, reason: 'index ' + idx + ' out of range (' + matches.length + ' matches)' }};
          const chosen = matches[pick].el;
          const r = chosen.getBoundingClientRect();
          chosen.click();
          return {{ ok: true, tag: chosen.tagName, role: chosen.getAttribute('role'),
                   box: {{ x: r.x, y: r.y, w: r.width, h: r.height }},
                   matchIndex: pick + 1, totalMatches: matches.length }};
        }})()
        """
        result = await _eval_js(browser_session, expr)
        if "error" in result:
            return ActionResult(
                extracted_content=f"click_by_text error: {result['error']}",
                error=result["error"],
            )
        val = result["value"]
        if isinstance(val, dict) and val.get("ok"):
            msg = f"click_by_text({params.text!r}) -> clicked {val.get('tag')}"
            _logger.info(msg)
            return ActionResult(
                extracted_content=_summarize_value(val), long_term_memory=msg
            )
        return ActionResult(
            extracted_content=f"click_by_text({params.text!r}) -> not found",
            error="not_found",
        )

    @tools.registry.action(
        (
            "Clear and set the content of a contenteditable element, "
            "hashtags included. Built for DraftJS (TikTok's caption "
            "field), which ignores `.value = ''`, Ctrl+A + Delete, and "
            "execCommand. Any text TikTok prefilled (the uploaded file "
            "name) is removed first, so the field ends up with exactly "
            "the text you pass. Plain text is inserted via a synthetic paste "
            "event; each #hashtag in the text is inserted on its own and "
            "then chosen from TikTok's suggestion dropdown, which is what "
            "makes it render as a real (highlighted) hashtag instead of "
            "inert text. Pass the WHOLE caption including hashtags in one "
            "call — do not add hashtags in separate steps. Returns the "
            "resulting text plus how many hashtags were highlighted."
        ),
        param_model=SetContenteditableAction,
    )
    async def set_contenteditable(
        params: SetContenteditableAction, browser_session
    ):
        clean_text = params.text.replace("\n", " ").replace("\r", " ")
        expr = _CAPTION_JS.replace(
            "__SELECTOR__", json.dumps(params.selector)
        ).replace("__TEXT__", json.dumps(clean_text))
        result = await _eval_js(browser_session, expr)
        if "error" in result:
            return ActionResult(
                extracted_content=f"set_contenteditable error: {result['error']}",
                error=result["error"],
            )
        val = result["value"]
        if isinstance(val, dict) and val.get("ok"):
            content = val.get("content", "")
            wanted = val.get("hashtagsWanted", 0)
            got = val.get("hashtagsHighlighted", 0)
            # innerText comes back with the editor's own spacing, so
            # compare loosely — an exact match fails on whitespace alone.
            matches = " ".join(content.split()) == " ".join(clean_text.split())
            msg = (
                f"set_contenteditable -> content={content!r} "
                f"(matches expected: {matches}, "
                f"draftAccepted: {val.get('draftAccepted')}, "
                f"cleared: {val.get('cleared')} via {val.get('clearMethod')}, "
                f"hashtags highlighted: {got}/{wanted})"
            )
            _logger.info(msg)
            if not val.get("cleared"):
                _logger.warning(
                    "caption prefill %r was not cleared — the caption may "
                    "still contain it",
                    val.get("prefill"),
                )
            if got < wanted:
                missed = [
                    r.get("tag")
                    for r in (val.get("results") or [])
                    if not r.get("picked")
                ]
                _logger.warning(
                    "caption hashtags not highlighted: %s", missed or "unknown"
                )
            return ActionResult(extracted_content=msg, long_term_memory=msg[:200])
        return ActionResult(
            extracted_content=f"set_contenteditable -> {val}", error="failed"
        )

    @tools.registry.action(
        (
            "Read the visible text (innerText) of the first element "
            "matching a CSS selector. Useful for verifying that a "
            "previous action took effect (e.g. confirming a caption is "
            "set, or that a date/time field shows the expected value)."
        ),
        param_model=GetTextAction,
    )
    async def get_text(params: GetTextAction, browser_session):
        sel_json = json.dumps(params.selector)
        expr = f"""
        (() => {{
          const el = document.querySelector({sel_json});
          if (!el) return null;
          return el.innerText || el.textContent || '';
        }})()
        """
        result = await _eval_js(browser_session, expr)
        if "error" in result:
            return ActionResult(
                extracted_content=f"get_text error: {result['error']}",
                error=result["error"],
            )
        val = result["value"]
        if val is None:
            return ActionResult(
                extracted_content=f"get_text({params.selector!r}) -> not found",
                error="not_found",
            )
        text = str(val)
        return ActionResult(
            extracted_content=f"get_text({params.selector!r}) -> {text!r}",
            long_term_memory=text[:200],
        )

    # -------------------------------------------------------------------
    # TikTok-specific high-level tools
    # -------------------------------------------------------------------

    class UploadVideoAction(BaseModel):
        """Upload a video file to TikTok Studio."""
        file_path: str = Field(
            description="Absolute path to the video file on disk."
        )

    @tools.registry.action(
        "Upload a video file to TikTok Studio. Finds the hidden file "
        "input via CDP and sets the file directly — no need to click "
        "buttons or find element indices. Just pass the file path.",
        param_model=UploadVideoAction,
    )
    async def upload_video(params: UploadVideoAction, browser_session):
        import asyncio
        import os
        file_path = params.file_path.strip()
        if not os.path.exists(file_path):
            return ActionResult(
                extracted_content=f"upload_video -> file not found: {file_path}",
                error="file_not_found",
            )
        # Check if file input exists, if not click "Select videos" to reveal it
        check = await _eval_js(browser_session, """
            (() => {
              const input = document.querySelector('input[type="file"]');
              if (input) return {ok: true};
              const btn = [...document.querySelectorAll('button')]
                .find(b => /select/i.test(b.innerText));
              if (btn) { btn.click(); return {ok: false, reason: 'clicked Select to reveal input'}; }
              return {ok: false, reason: 'no file input and no Select button'};
            })()
        """)
        if isinstance(check.get("value"), dict) and not check["value"].get("ok"):
            await asyncio.sleep(1)
        # Use CDP to set the file directly
        cdp = await browser_session.get_or_create_cdp_session()
        try:
            doc = await cdp.cdp_client.send.DOM.getDocument(
                params={}, session_id=cdp.session_id
            )
            root_id = doc["root"]["nodeId"]
            query = await cdp.cdp_client.send.DOM.querySelector(
                params={"nodeId": root_id, "selector": 'input[type="file"]'},
                session_id=cdp.session_id,
            )
            node_id = query.get("nodeId")
            if not node_id:
                return ActionResult(
                    extracted_content="upload_video -> no input[type=file] found in DOM",
                    error="no_file_input",
                )
            await cdp.cdp_client.send.DOM.setFileInputFiles(
                params={"nodeId": node_id, "files": [file_path]},
                session_id=cdp.session_id,
            )
        except Exception as exc:
            return ActionResult(
                extracted_content=f"upload_video -> CDP error: {exc}",
                error=str(exc),
            )
        msg = f"upload_video -> uploaded {os.path.basename(file_path)}"
        _logger.info(msg)
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    class DismissOverlayAction(BaseModel):
        """Dismiss a 'Continue editing?' overlay if present."""

    @tools.registry.action(
        "Check for and dismiss the 'Continue editing?' overlay. "
        "If the overlay is present, clicks Discard twice (the second "
        "click confirms the 'Discard this post?' dialog). Returns "
        "whether an overlay was found and dismissed.",
        param_model=DismissOverlayAction,
    )
    async def dismiss_overlay(params: DismissOverlayAction, browser_session):
        check = await _eval_js(
            browser_session,
            "document.body.innerText.includes('Continue editing')",
        )
        if check.get("value") is not True:
            return ActionResult(
                extracted_content="dismiss_overlay -> no overlay found"
            )
        # First discard
        click1_js = """
        (() => {
          const target = 'discard';
          const all = Array.from(document.querySelectorAll('*'));
          const matches = [];
          for (const el of all) {
            if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
            const t = (el.innerText || '').toLowerCase();
            if (t && t.includes(target)) {
              const r = el.getBoundingClientRect();
              if (r.width > 0 && r.height > 0) matches.push({el, area: r.width * r.height});
            }
          }
          if (!matches.length) return {ok: false, reason: 'no Discard found'};
          matches.sort((a, b) => a.area - b.area);
          matches[0].el.click();
          return {ok: true, step: 1};
        })()
        """
        await _eval_js(browser_session, click1_js)
        import asyncio
        await asyncio.sleep(1)
        # Second discard (confirmation dialog inserts a new button)
        click2_js = """
        (() => {
          const target = 'discard';
          const all = Array.from(document.querySelectorAll('*'));
          const matches = [];
          for (const el of all) {
            if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
            const t = (el.innerText || '').toLowerCase();
            if (t && t.includes(target)) {
              const r = el.getBoundingClientRect();
              if (r.width > 0 && r.height > 0) matches.push({el, area: r.width * r.height});
            }
          }
          if (matches.length < 2) return {ok: false, reason: 'second Discard not found'};
          matches.sort((a, b) => a.area - b.area);
          matches[1].el.click();
          return {ok: true, step: 2};
        })()
        """
        r2 = await _eval_js(browser_session, click2_js)
        if "error" in r2 or not (isinstance(r2.get("value"), dict) and r2["value"].get("ok")):
            return ActionResult(
                extracted_content="dismiss_overlay -> first Discard clicked but confirmation failed",
                error="confirmation_failed",
            )
        return ActionResult(
            extracted_content="dismiss_overlay -> overlay dismissed (both Discards clicked)"
        )

    class SelectCoverFrameAction(BaseModel):
        """Select the first frame in the cover editor and save."""

    @tools.registry.action(
        "Open the cover editor, select the first (leftmost) video frame "
        "as the cover thumbnail, and save. Call this after the video has "
        "finished uploading to fix the default black cover.",
        param_model=SelectCoverFrameAction,
    )
    async def select_cover_frame(params: SelectCoverFrameAction, browser_session):
        import asyncio
        # Click "Edit cover"
        open_js = """
        (() => {
          const all = Array.from(document.querySelectorAll('*'));
          for (const el of all) {
            if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
            if ((el.innerText || '').trim().toLowerCase() === 'edit cover') {
              el.click(); return {ok: true, step: 'opened'};
            }
          }
          return {ok: false, reason: 'Edit cover button not found'};
        })()
        """
        r = await _eval_js(browser_session, open_js)
        if "error" in r or not (isinstance(r.get("value"), dict) and r["value"].get("ok")):
            return ActionResult(
                extracted_content="select_cover_frame -> Edit cover button not found",
                error="not_found",
            )
        await asyncio.sleep(2)
        # Click leftmost frame on the FramePicker canvas
        frame_js = """
        (() => {
          const canvas = document.querySelector('.FramePicker__canvas');
          if (!canvas) return {ok: false, reason: 'FramePicker__canvas not found'};
          const r = canvas.getBoundingClientRect();
          const x = r.x + 5;
          const y = r.y + r.height / 2;
          canvas.dispatchEvent(new MouseEvent('mousedown', {clientX: x, clientY: y, bubbles: true}));
          canvas.dispatchEvent(new MouseEvent('mouseup', {clientX: x, clientY: y, bubbles: true}));
          canvas.dispatchEvent(new MouseEvent('click', {clientX: x, clientY: y, bubbles: true}));
          return {ok: true, step: 'frame_selected', x: Math.round(x), y: Math.round(y)};
        })()
        """
        r2 = await _eval_js(browser_session, frame_js)
        await asyncio.sleep(1)
        # Click Save
        save_js = """
        (() => {
          const all = Array.from(document.querySelectorAll('*'));
          const matches = [];
          for (const el of all) {
            if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
            if ((el.innerText || '').trim().toLowerCase() === 'save') {
              const r = el.getBoundingClientRect();
              if (r.width > 0 && r.height > 0) matches.push({el, area: r.width * r.height});
            }
          }
          if (!matches.length) return {ok: false, reason: 'Save button not found'};
          matches.sort((a, b) => a.area - b.area);
          matches[0].el.click();
          return {ok: true, step: 'saved'};
        })()
        """
        r3 = await _eval_js(browser_session, save_js)
        frame_info = r2.get("value", {}) if isinstance(r2.get("value"), dict) else {}
        save_info = r3.get("value", {}) if isinstance(r3.get("value"), dict) else {}
        msg = f"select_cover_frame -> frame={frame_info.get('ok')}, save={save_info.get('ok')}"
        _logger.info(msg)
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    class SetScheduleDateAction(BaseModel):
        """Click a day in the TikTok date picker calendar."""
        day: str = Field(description="Day number to click, e.g. '15'.")

    @tools.registry.action(
        "Open the date picker and click a day number. The date picker "
        "shows a calendar for the current month. Just pass the day "
        "number as a string (e.g. '15').",
        param_model=SetScheduleDateAction,
    )
    async def set_schedule_date(params: SetScheduleDateAction, browser_session):
        import asyncio
        open_js = "document.querySelectorAll('input.TUXTextInputCore-input')[1].click()"
        await _eval_js(browser_session, open_js)
        await asyncio.sleep(0.5)
        day_json = json.dumps(params.day.strip())
        click_js = f"""
        (() => {{
          const cells = [...document.querySelectorAll('*')].filter(
            e => e.children.length === 0 && e.offsetParent &&
                 e.innerText.trim() === {day_json} &&
                 e.closest('[class*=calendar], [class*=Calendar], [class*=datepicker], [class*=DatePicker]')
          );
          if (!cells.length) return {{ok: false, reason: 'day ' + {day_json} + ' not found in calendar'}};
          cells[0].click();
          return {{ok: true, day: {day_json}}};
        }})()
        """
        r = await _eval_js(browser_session, click_js)
        val = r.get("value", {})
        if isinstance(val, dict) and val.get("ok"):
            return ActionResult(
                extracted_content=f"set_schedule_date -> clicked day {params.day}"
            )
        reason = val.get("reason", "unknown") if isinstance(val, dict) else str(val)
        return ActionResult(
            extracted_content=f"set_schedule_date -> failed: {reason}",
            error="failed",
        )

    class SetScheduleTimeAction(BaseModel):
        """Set the hour and minute in the TikTok time picker."""
        hour: str = Field(description="Hour to select, e.g. '13'.")
        minute: str = Field(description="Minute to select, e.g. '30'.")

    @tools.registry.action(
        "Open the time picker and click the hour and minute values. "
        "Pass hour and minute as strings (e.g. hour='13', minute='30'). "
        "Minutes must be multiples of 5.",
        param_model=SetScheduleTimeAction,
    )
    async def set_schedule_time(params: SetScheduleTimeAction, browser_session):
        import asyncio
        open_js = "document.querySelectorAll('input.TUXTextInputCore-input')[0].click()"
        await _eval_js(browser_session, open_js)
        await asyncio.sleep(0.5)
        hh_json = json.dumps(params.hour.strip())
        mm_json = json.dumps(params.minute.strip())
        # The picker has two columns sharing the same class. Hours are
        # in the first column container, minutes in the second. We find
        # the column containers and click within each separately.
        click_hour_js = f"""
        (() => {{
          const columns = document.querySelectorAll('.tiktok-timepicker-column, [class*=timepicker-column]');
          if (columns.length >= 2) {{
            const hOpts = [...columns[0].querySelectorAll('.tiktok-timepicker-option-text')];
            const hEl = hOpts.find(e => e.innerText.trim() === {hh_json});
            if (hEl) {{ hEl.click(); return {{ok: true, hour: {hh_json}}}; }}
          }}
          const opts = [...document.querySelectorAll('.tiktok-timepicker-option-text')];
          const hEl = opts.find(e => e.innerText.trim() === {hh_json});
          if (!hEl) return {{ok: false, reason: 'hour ' + {hh_json} + ' not found'}};
          hEl.click();
          return {{ok: true, hour: {hh_json}}};
        }})()
        """
        await _eval_js(browser_session, click_hour_js)
        await asyncio.sleep(0.3)
        click_min_js = f"""
        (() => {{
          const columns = document.querySelectorAll('.tiktok-timepicker-column, [class*=timepicker-column]');
          if (columns.length >= 2) {{
            const mOpts = [...columns[1].querySelectorAll('.tiktok-timepicker-option-text')];
            const mEl = mOpts.find(e => e.innerText.trim() === {mm_json});
            if (mEl) {{ mEl.click(); return {{ok: true, minute: {mm_json}}}; }}
          }}
          const opts = [...document.querySelectorAll('.tiktok-timepicker-option-text')];
          const all = opts.filter(e => e.innerText.trim() === {mm_json});
          const mEl = all.length > 1 ? all[1] : all[0];
          if (!mEl) return {{ok: false, reason: 'minute ' + {mm_json} + ' not found'}};
          mEl.click();
          return {{ok: true, minute: {mm_json}}};
        }})()
        """
        r = await _eval_js(browser_session, click_min_js)
        # Close picker by clicking "When to post"
        close_js = """
        (() => {
          const all = Array.from(document.querySelectorAll('*'));
          for (const el of all) {
            if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
            if ((el.innerText || '').trim() === 'When to post') {
              el.click(); return {ok: true};
            }
          }
          return {ok: false};
        })()
        """
        await _eval_js(browser_session, close_js)
        val = r.get("value", {})
        if isinstance(val, dict) and val.get("ok"):
            return ActionResult(
                extracted_content=f"set_schedule_time -> set to {params.hour}:{params.minute}"
            )
        reason = val.get("reason", "unknown") if isinstance(val, dict) else str(val)
        return ActionResult(
            extracted_content=f"set_schedule_time -> failed: {reason}",
            error="failed",
        )

    class GetScheduleValuesAction(BaseModel):
        """Read the current date and time from the schedule inputs."""

    @tools.registry.action(
        "Read the current values of the schedule date and time inputs. "
        "Returns [time, date] strings.",
        param_model=GetScheduleValuesAction,
    )
    async def get_schedule_values(params: GetScheduleValuesAction, browser_session):
        js = """
        (() => {
          const inputs = [...document.querySelectorAll('input.TUXTextInputCore-input')]
            .filter(e => e.value.match(/\\d/));
          return inputs.map(e => e.value);
        })()
        """
        r = await _eval_js(browser_session, js)
        val = r.get("value", [])
        msg = f"get_schedule_values -> {val}"
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    class ScrollToSubmitAction(BaseModel):
        """Scroll the submit button into view."""

    @tools.registry.action(
        "Scroll the Schedule/Post submit button into view so it can be "
        "clicked. Call this before clicking the submit button.",
        param_model=ScrollToSubmitAction,
    )
    async def scroll_to_submit(params: ScrollToSubmitAction, browser_session):
        js = """
        (() => {
          const btn = [...document.querySelectorAll('button')].find(
            e => e.className.includes('Button__root') &&
                 /schedule|post|agendar|postar/i.test(e.innerText.trim())
          );
          if (!btn) return {ok: false, reason: 'submit button not found'};
          btn.scrollIntoView({behavior: 'smooth', block: 'center'});
          return {ok: true, text: btn.innerText.trim()};
        })()
        """
        r = await _eval_js(browser_session, js)
        val = r.get("value", {})
        if isinstance(val, dict) and val.get("ok"):
            return ActionResult(
                extracted_content=f"scroll_to_submit -> scrolled to '{val.get('text')}'"
            )
        return ActionResult(
            extracted_content="scroll_to_submit -> button not found",
            error="not_found",
        )

    return tools


__all__ = ["build_tools"]
