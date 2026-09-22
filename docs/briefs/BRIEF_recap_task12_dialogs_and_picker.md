# Task 12 — dialogs that tell you what happened, and a picker that remembers

`app/gui_app/helpers.py`, `app/gui_app/style.py`, `app/gui_app/mixins/recap.py`,
`app/recap_media/artifacts.py`. Small, and it pays for itself immediately —
Recap debugging is now the main activity and every error goes through these
dialogs.

## What happened

Validating an edited recap script produced a popup reading:

```
The selected recap script's artifact folder does not
```

The real message (`artifacts.py` ~line 270) is:

```
The selected recap script's artifact folder does not match the
loaded source 's17e9a duct tape dystopia.mp4'.
```

Everything after "does not" was clipped — including the only part that says what
was expected. The resolver was correct: `output/recap/episode_identity.json`
records `s17e7a my tighty whiteys.mp4`, a different episode, and that folder has
both Track A companions, so it is treated as authoritative and correctly refused.
The user had edited `output/recap_duct_tape_dystopia_v2/recap_script.json` but
the picker reopened at the stale folder, and both files are named
`recap_script.json`.

Three defects, none of them in the staleness logic.

## Change 1 — message boxes must wrap

`style.py` sets `min-width: 300px` on `#qt_msgbox_label` but nothing turns on
word wrap, and the static `QMessageBox.warning(...)` helpers give no access to
the label. Long single-line messages get clipped rather than wrapped.

Add a helper in `app/gui_app/helpers.py`:

```python
def show_message(parent, icon, title, text, *, detail=None):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    label = box.findChild(QLabel, "qt_msgbox_label")
    if label is not None:
        label.setWordWrap(True)
    return box.exec()
```

Convert the 16 `QMessageBox.warning / .information / .critical` call sites to it.
Mechanical, but do it in one pass so no dialog is left able to clip.

In `style.py`, give `#qt_msgbox_label` a `max-width` (around 520px) alongside the
existing `min-width`, so wrapping has a sane column rather than one long line the
window tries to grow to.

## Change 2 — the picker starts where the work is

`recap.py` line ~545:

```python
path, _ = QFileDialog.getOpenFileName(
    self, "Import AI Recap Script", "", "JSON files (*.json)",
)
```

The empty third argument is the start directory, so the dialog opens wherever Qt
last happened to be. With several folders all containing a file called
`recap_script.json`, that is how the wrong one gets picked.

Pass a real start directory, in this order of preference:

1. the folder of the currently bound artifact context, if one is bound;
2. the last folder a script was successfully validated from, persisted in
   settings under a new key;
3. `output/`.

Persist (2) only on a *successful* validation — remembering a folder that just
failed would make the mistake sticky.

## Change 3 — the error names both folders

The message says what was expected but not what was chosen. Since the whole
problem is two identically-named files, the folder is the identifying
information. Change the `RecapInputError` in
`resolve_recap_artifact_context_for_script` to read:

```
The recap script in 'output/recap' belongs to 's17e7a my tighty whiteys.mp4',
but the loaded source is 's17e9a duct tape dystopia.mp4'.
Pick the script from the artifact folder for this episode.
```

Folder name and both source names, plus what to do about it. Use the folder name
only, not the absolute path — the full path is long enough to reintroduce the
wrapping problem it just escaped.

## Not in scope

`output/recap/` is stale scaffolding from an older episode and is now a trap: it
has complete Track A companions for the wrong source, so it will keep winning
resolution races. Deleting or renaming it is the user's call, not a code change —
mention it, do not do it.

The deleted-segment hole flagged during task 11 (a removed segment leaves an
orphaned clip in the plan) also stays out of this task.

## Tests

- `show_message` enables word wrap on the message label.
- The stylesheet has both `min-width` and `max-width` on `#qt_msgbox_label`.
- No `QMessageBox.warning(` / `.information(` / `.critical(` static calls remain
  under `app/gui_app/` (a grep assertion is fine and will catch regressions).
- The picker is given a non-empty start directory when a context is bound.
- The last-validated folder is persisted only after a successful validation.
- The mismatch error contains the selected folder name and both source names.

## Live test

1. Load `s17e9a duct tape dystopia.mp4`.
2. Import a script from `output/recap/` — the wrong folder, deliberately. The
   dialog must be fully readable and name both episodes.
3. Import again. The picker must open in the folder you last succeeded from, not
   back at the default.
4. Then repeat the task 11 live test, which is what this was blocking: edit a
   segment's text in `recap_duct_tape_dystopia_v2/recap_script.json`, regenerate
   narration, Open in Editor, and confirm the new wording is audible without
   restarting.
