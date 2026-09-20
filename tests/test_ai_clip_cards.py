from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui_app.main_window import ShortsFactoryWindow


def _flat(text):
    return " ".join(text.split(chr(10))[2:])


def _card_text(candidate):
    app = QApplication.instance() or QApplication([])
    window = ShortsFactoryWindow()
    try:
        base = {
            "rank": 1,
            "start_ms": 438_000,
            "end_ms": 473_000,
            "score": 89,
            "hook": "",
            "description": "You both signed the booth rental agreement. I get 99% of profits.",
            "reason": "",
        }
        window.ai_candidates = [{**base, **candidate}]
        window.populate_clip_cards()
        app.processEvents()
        card = window.clip_cards[0]
        return card.text(), card.toolTip()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_card_leads_with_title_then_hook_and_shows_duration():
    text, _tip = _card_text(
        {"title": "Booth rental agreement revealed", "hook": "You both signed it."}
    )
    lines = text.split("\n")

    assert lines[0].startswith("AI PICK #1")
    assert "35.0s" in lines[1]
    body = _flat(text)
    assert body.index("Booth rental agreement revealed") < body.index("You both signed it.")
    assert "Anchored by" not in text


def test_card_without_title_falls_back_to_hook_then_quote():
    with_hook, _ = _card_text({"title": "", "hook": "You both signed it."})
    assert _flat(with_hook).endswith("You both signed it.")

    quote_only, _ = _card_text({"title": "", "hook": ""})
    assert "You both signed the booth rental" in _flat(quote_only)


def test_generic_title_falls_through_to_hook():
    text, _ = _card_text({"title": "In this episode, a fight", "hook": "You both signed it."})

    assert "In this episode" not in text
    assert _flat(text).endswith("You both signed it.")


def test_transcript_quote_is_in_tooltip_and_generic_reason_is_hidden():
    _text, tip = _card_text(
        {
            "title": "Booth rental agreement revealed",
            "hook": "You both signed it.",
            "reason": "clear setup and payoff",
        }
    )

    assert "booth rental agreement. I get 99%" in tip
    assert "Why selected" not in tip


def test_generic_hook_is_dropped_not_replaced_by_a_transcript_quote():
    text, _tip = _card_text({"title": "Booth rental agreement revealed", "hook": "Interesting moment"})

    assert "You both signed" not in text


def _populate(count):
    app = QApplication.instance() or QApplication([])
    window = ShortsFactoryWindow()
    try:
        window.ai_candidates = [
            {
                "rank": index + 1,
                "start_ms": index * 60_000,
                "end_ms": index * 60_000 + 30_000,
                "score": 90 - index,
                "title": f"Clip number {index + 1} title here",
                "hook": "",
                "description": "words",
                "reason": "",
            }
            for index in range(count)
        ]
        window.populate_clip_cards()
        app.processEvents()
        return [card.isHidden() for card in window.clip_cards]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_ten_candidates_render_ten_cards():
    hidden = _populate(10)

    assert len(hidden) >= 10
    assert not any(hidden[:10])
