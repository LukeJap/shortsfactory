# Task 15 — undo, then clipboard, for editor entities

`app/gui_app/mixins/` (new `editor_history.py`), `main_window.py`,
`editor_assets.py`. Do **undo first** — it is the safety net that makes paste
safe to experiment with.

This is backlog, not next. Finish the task 11/13 live test before starting.

## What the code already gives us

Every editor-plan mutation in the app follows one shape:

```python
self.editor_asset_plan = upsert_clip(self.editor_asset_plan, clip)
self.save_editor_asset_plan_state()
```

22 call sites (13 in `editor_assets.py`, 9 in `recap.py`), all ending at
`save_editor_asset_plan_state()` (`editor_assets.py` ~line 180). That single
choke point is why snapshot undo is the right design here: hook it once and
every mutation becomes undoable, including ones added later.

A `QUndoStack`/`QUndoCommand` per operation is the textbook approach and the
wrong one for this codebase — it would mean rewriting all 22 sites and every
future one, for no benefit over snapshots of a ~16 KB dict.

There is currently **no keyboard shortcut anywhere in the app** — no `QShortcut`,
no `QKeySequence`. This task builds that layer.

## Part 1 — undo/redo

New `EditorHistoryMixin` holding two bounded stacks of deep copies.

Capture without touching call sites: keep `self._last_saved_plan`, and in
`save_editor_asset_plan_state()` push that (the state *before* this save) onto
the undo stack, then refresh it to the current plan. A re-entrancy flag stops
undo's own save from pushing.

```python
def save_editor_asset_plan_state(self):
    if not self._restoring_history:
        self._push_undo(self._last_saved_plan)   # prior state
        self._redo_stack.clear()
    self._last_saved_plan = copy.deepcopy(self.editor_asset_plan)
    ... existing body unchanged ...
```

Undo: pop → assign → save with `_restoring_history` set → `refresh_editor_asset_timeline()`
→ clear any selection whose clip no longer exists. Redo mirrors it.

Bound both stacks at 50. The plan is ~16 KB, so worst case is under a megabyte.

### What undo must NOT cover

Be explicit about this or it will produce bad surprises:

- **Generation stages** — Generate Voiceover, Generate Recap, Open in Editor.
  These spawn renders, call Orpheus and write MP4s. Undo cannot un-render a
  video and must not pretend to.
- **Final renders.**
- **Edits made to `recap_script.json` outside the app.**

When a generation stage rebuilds the plan, **clear both stacks**. Undoing across
a regeneration boundary would restore clips that no longer match the rendered
base, which is exactly the class of stale-state bug tasks 11-13 were about.

## Part 2 — copy/paste for SFX and emoji

Selection state already exists (`selected_sfx_clip_id`, `selected_emoji_clip_id`).

- **Ctrl+C** — deep copy the selected clip into an in-process clipboard, keeping
  its kind. In-process is enough; do not involve the system clipboard.
- **Ctrl+V** — for each clipboard entry: new unique id, `start` moved to the
  playhead, duration preserved, `end = start + duration`. Route to the lane
  matching the **copied clip's kind**, not the current selection. Then
  `upsert_clip` → `save_editor_asset_plan_state()` → refresh, so it lands on the
  undo stack for free.

Edge cases: paste with an empty clipboard is a no-op; a paste that would extend
past the selection end clamps or is refused with a status line, not silently
truncated; pasting the same clip repeatedly must produce distinct ids.

Select the newly pasted clip after paste — that is what makes repeated
paste-and-nudge feel right.

## Part 3 — the shortcut layer

Add `QShortcut`s on the main window with `Qt.ShortcutContext.WindowShortcut`:
Ctrl+Z undo, Ctrl+Y and Ctrl+Shift+Z redo, Ctrl+C copy, Ctrl+V paste.

**The one real trap:** the app has text entry — the VIDEO TITLE field and the
transcript EDIT TEXT mode. Ctrl+Z there must do text undo, not timeline undo.
Guard every handler with a focus check: if `QApplication.focusWidget()` is a
`QLineEdit`, `QPlainTextEdit` or `QTextEdit`, let the event through untouched.
Add a test for this; it is the kind of thing that only shows up when someone is
mid-sentence in a title field and loses their timeline instead.

## Tests

- A mutation then undo restores the previous plan exactly; redo reapplies it.
- Undo past the start of the stack is a no-op.
- The stack is bounded at 50 and drops oldest first.
- A generation stage clears both stacks.
- Copy then paste produces a clip with a new id at the playhead, same duration.
- Paste targets the copied clip's lane regardless of current selection.
- A shortcut is ignored while a text widget has focus.
- Undo after paste removes the pasted clip.

## Live test

Add an SFX, move it, resize it, paste a copy, delete something. Ctrl+Z five
times — each step reverses in order and the timeline redraws each time. Then
Ctrl+Y forward. Then type in the VIDEO TITLE field and press Ctrl+Z; the text
must undo, and the timeline must not move.
