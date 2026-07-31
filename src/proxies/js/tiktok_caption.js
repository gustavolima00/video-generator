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

  // Draft keeps its OWN selection state and only learns about a DOM
  // selection through the async selectionchange -> React onSelect path.
  // Selecting and pasting in the same tick therefore pastes at Draft's
  // STALE caret (the end of the prefill), which is exactly how captions
  // ended up as "story_04<caption>". Nudge the event and give it a tick.
  async function selectAll() {
    const el = editor();
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
    await sleep(150);
  }

  const content = () => (editor().innerText || '').trim();

  // Wipe whatever TikTok prefilled (the uploaded file's name). Draft drops
  // an EMPTY paste, so clearing means pasting a sentinel over the full
  // selection; the sentinel is then left selected so the caller's first
  // real paste replaces it in turn.
  async function clearAll() {
    const SENTINEL = '⁣'; // invisible separator: harmless if it survives
    for (let attempt = 1; attempt <= 2; attempt++) {
      await selectAll();
      insertPaste(SENTINEL);
      await sleep(300);
      const after = content();
      if (after === SENTINEL || after === '') {
        await selectAll();
        return { cleared: true, method: 'paste-replace', attempts: attempt };
      }
    }
    focusEnd();
    return { cleared: false, method: 'none', left: content() };
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
