"""TikTok Studio renders in the account's language, not always English.

The publisher stalled for weeks because every text matcher looked for English
labels while the UI had switched to Portuguese: `dismiss_overlay` reported "no
overlay found" with "Descartar" on screen, `select_cover_frame` could not find
"Editar capa", and the agent burned its whole step budget hunting for a
"Schedule" radio labelled "Agendar".
"""

import datetime

import pytest

from src.proxies.tiktok_publisher_proxy import BrowserUseTikTokPublisherProxy
from src.proxies.tiktok_publisher_tools import _JS_HELPERS, _LABELS, _js, _variants

PORTUGUESE = {
    "continue_editing": "continuar editando",
    "discard": "descartar",
    "edit_cover": "editar capa",
    "save": "salvar",
    "when_to_post": "quando publicar",
    "schedule": "agendar",
}


@pytest.mark.parametrize("key,word", sorted(PORTUGUESE.items()))
def test_every_label_has_a_portuguese_variant(key, word):
    assert word in _LABELS[key], f"{key} would not match a pt-BR UI"


@pytest.mark.parametrize("key", sorted(_LABELS))
def test_label_variants_are_lowercase(key):
    # The matchers lowercase the DOM text before comparing, so an uppercase
    # variant here silently never matches.
    for variant in _LABELS[key]:
        assert variant == variant.lower()


def test_variants_renders_a_json_array_for_js():
    assert _variants("discard") == '["discard", "descartar"]'


def test_variants_can_merge_several_labels():
    merged = _variants("schedule", "when_to_post")
    assert "agendar" in merged and "quando publicar" in merged


def test_js_expands_helpers_and_labels():
    out = _js("__HELPERS__ const x = __ttFind(__SCHEDULE__, true);")

    assert "__HELPERS__" not in out
    assert "__SCHEDULE__" not in out
    assert "__ttFind" in out
    assert "agendar" in out


def test_js_leaves_unrelated_code_alone():
    assert _js("document.title") == "document.title"


def test_helpers_define_the_language_independent_schedule_probe():
    # Activation is confirmed by the date/time fields appearing rather than
    # by any label, which is what makes it survive a language switch.
    assert "__ttScheduleInputs" in _JS_HELPERS
    assert "TUXTextInputCore-input" in _JS_HELPERS


def _task(schedule_at=datetime.datetime(2026, 8, 24, 18, 0)):
    return BrowserUseTikTokPublisherProxy._build_task(
        "/tmp/v.mp4", "Legenda #fyp", schedule_at
    )


def test_prompt_schedules_via_the_verified_tool():
    task = _task()
    assert "activate_schedule()" in task
    assert "submit_post()" in task


def test_prompt_no_longer_clicks_an_english_only_label():
    # click_by_text('Schedule') is exactly what returned not_found on the
    # Portuguese UI and sent the agent into the expand/click loop.
    task = _task()
    assert "click_by_text(text='Schedule'" not in task


def test_prompt_warns_the_agent_the_language_varies():
    assert "Portuguese" in _task()


def test_prompt_tells_the_agent_not_to_hunt_by_index():
    # The loop that ate 37 steps was index-clicking a re-rendering block.
    assert "indices" in _task()


def test_immediate_post_path_also_uses_submit_post():
    task = BrowserUseTikTokPublisherProxy._build_task("/tmp/v.mp4", "Legenda", None)
    assert "submit_post()" in task
    assert "Click 'Post' / 'Postar'" not in task
