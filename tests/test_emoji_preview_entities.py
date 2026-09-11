from gui_app.mixins.emoji_preview import EmojiPreviewMixin


class PreviewHarness(EmojiPreviewMixin):
    def __init__(self, context_matches, clips):
        self.context_matches = context_matches
        self.editor_asset_plan = {"clips": clips}

    def editor_asset_context_matches_current_selection(self):
        return self.context_matches


def test_emoji_preview_requires_generated_editor_entities():
    preview = PreviewHarness(
        False,
        [
            {
                "id": "old-emoji",
                "kind": "EMOJI",
                "start": 1.0,
                "end": 2.0,
                "active": True,
            }
        ],
    )

    assert preview.active_emoji_preview_events(1500) == []


def test_emoji_preview_uses_the_same_generated_entity_as_the_timeline():
    clip = {
        "id": "emoji-1",
        "kind": "EMOJI",
        "start": 1.0,
        "end": 2.0,
        "active": True,
    }
    preview = PreviewHarness(True, [clip])

    assert preview.active_emoji_preview_events(1500) == [
        ("editor_plan", "emoji-1", clip)
    ]
