# Task 13 — Open in Editor breaks script validation

`app/gui_app/mixins/recap.py`. Small fix, and it unblocks the task 11 test that
has now failed twice for this reason rather than for the reason being tested.

## The bug

`import_external_recap_script` (line ~649):

```python
source = getattr(self, "video_path", None)
context = resolve_recap_artifact_context_for_script(source, selected_path, ...)
```

`load_video` in `playback.py` sets `self.video_path = path` unconditionally, and
Open in Editor deliberately loads the caption-free base render as the editor's
media. So from the first Open in Editor onward, `video_path` is
`final_recap_editor_base.mp4`, not the episode.

Every subsequent Validate Script then compares the artifact folder's identity
against the base render's filename and fails:

```
ERROR: The recap script in 'output/recap_duct_tape_dystopia_v2' belongs to
's17e9a duct tape dystopia.mp4', but the loaded source is
'final_recap_editor_base.mp4'.
```

The message is correct and the resolver is correct. The input is wrong.

**This is what forced the app restart in the previous session.** There was no
cache problem at that step: re-validating in-session was impossible, so the
edited script never became authoritative, and relaunching "fixed" it only
because startup resets `video_path` to the real episode. Task 11's hash work is
very likely fine and has simply never been exercised.

## The precedent already in the file

`_active_recap_artifact_context` (line ~395) handles exactly this situation:

```python
active.source_video == source
or (
    getattr(self, "recap_editor_mode", False)
    and source in {active.final_recap_path, active.editor_base_recap_path}
)
```

It knows the loaded media may legitimately be the recap render rather than the
episode. The import path never got the same treatment.

## The fix

Resolve the *episode* rather than whatever media is loaded. In
`import_external_recap_script`, replace the raw `video_path` read with a helper:

```python
def _recap_episode_source(self) -> Path | None:
    """The episode an artifact context is about, not the media on screen."""
    active = getattr(self, "recap_artifact_context", None)
    if active is not None and getattr(active, "source_video", None):
        return Path(active.source_video)
    inputs = getattr(self, "_recap_inputs", None)
    if inputs is not None and getattr(inputs, "episode_identity", None):
        try:
            return resolve_recap_source_video(inputs.episode_identity)
        except RecapInputError:
            pass
    path = getattr(self, "video_path", None)
    return Path(path) if path else None
```

`resolve_recap_source_video` is already imported (line 116) and already used this
way at lines 923 and 1178. Use the helper anywhere else that passes `video_path`
into recap artifact resolution — audit for other call sites while you are there.

If the helper returns the base render (no context bound, nothing else known),
keep the existing error. Failing loudly is right when the episode genuinely
cannot be determined.

## Also — the source label lies

`_set_recap_episode_context` builds its label from `video_path`, so after Open in
Editor the panel reads `Source: final_recap_editor_base.mp4`. Point it at the
same helper so it names the episode.

## Not a bug: the export did not pick up the edit

The log shows the no-replanning path:

```
=== AI RECAP EDITOR EXPORT ===
Consuming current Recap editor entities; no Standard Short planning will run.
Editor base: ...\final_recap_editor_base.mp4
Combined Recap captions: 307
```

That path consumes the existing base render and the existing caption plan by
design. Narration is baked into the base, so an export alone can never contain a
script change. The captions were stale for the same reason — they regenerate when
the sequence rebuilds, not at export.

The working loop, once this fix lands:

```
edit recap_script.json
  -> Validate Script          (now succeeds in-session)
  -> Generate Narration       (Orpheus must be running)
  -> Open in Editor           (base re-renders, captions regenerate)
  -> Render Final Video
```

Skipping Generate Narration is why the change never appeared. Worth considering
a follow-up: when Open in Editor detects a narration hash mismatch with no
matching WAV, say so plainly — "3 segments have no narration for their current
text; run Generate Narration" — rather than silently rendering the old audio.

## Tests

- With a bound artifact context and `video_path` pointing at the base render,
  import resolves against the episode and succeeds.
- With no context bound, it falls back to `video_path` and the existing error
  still fires for a genuine mismatch.
- The source label shows the episode filename while in recap editor mode.

## Live test

1. Load `s17e9a duct tape dystopia.mp4`, validate, generate narration, Open in
   Editor.
2. **Without restarting**, edit a segment's text, Validate Script — it must
   succeed now instead of erroring.
3. Generate Narration, Open in Editor.
4. The new wording must be audible, and the on-screen captions must show it too.
