"""
ShortsFactoryWindow, the QMainWindow that composes the entire desktop
app, and its main() launcher. Owns window/UI construction (build_ui() and
friends: the three-column layout described in SHORTSFACTORY.md's Project
Context), QSettings load/save, QProcess setup for every subprocess the
GUI can launch (render, transcript preload, emoji preview planning),
keyboard shortcuts, and the single app-wide eventFilter() that the
emoji/caption placement-editor drag interactions route through. Everything else lives in
mixins/ -- see each mixin file's own docstring for what it owns; this
class's own body is mostly __init__ and layout construction, with method
bodies for each functional area coming from whichever mixin implements
them.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QPoint, QProcess, QProcessEnvironment, QSettings, QTimer
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from editor_asset_plan import load_editor_asset_plan
from visual_emphasis import DEFAULT_ENERGY, normalize_energy, normalize_sfx_mode

from .constants import ROOT
from .settings_keys import (
    AUTO_CUTS_ENABLED,
    EDIT_ENERGY,
    EMOJI_ENABLED,
    FILTERS_ENABLED,
    FX_INTENSITY,
    MIN_EMOJI_EVENTS,
    PREVIEW_VOLUME,
    RECAP_NARRATION_PITCH_SEMITONES,
    RECAP_SOURCE_PITCH_SEMITONES,
    RECAP_SCRIPT_SOURCE,
    RECAP_SPEED,
    RECAP_TARGET_DURATION_SECONDS,
    RECAP_VOICE,
    SFX_MODE,
    TRANSCRIPTION_QUALITY,
)
from .style import STYLESHEET
from .timeline_widget import SuggestionSlider
from .widgets import (
    AspectRatioContainer,
    DropZone,
    ProgramMonitorComposition,
    TimelineNavigator,
)

from recap_media.orpheus_provider import DEFAULT_VOICE as DEFAULT_ORPHEUS_VOICE
from recap_media.render import (
    DEFAULT_NARRATION_PITCH_SEMITONES,
    DEFAULT_SOURCE_PITCH_SEMITONES,
    NARRATION_PITCH_SEMITONES_RANGE,
)

from .mixins.ai_clip_hunter import AIClipHunterMixin
from .mixins.caption_preview import CaptionPreviewMixin
from .mixins.editor_assets import EditorAssetsMixin
from .mixins.emoji_preview import EmojiPreviewMixin
from .mixins.music import MusicMixin
from .mixins.playback import PlaybackMixin
from .mixins.persistent_title import PersistentTitleMixin
from .mixins.recap import RecapMixin
from .mixins.render_pipeline import RenderPipelineMixin
from .mixins.settings import SettingsMixin
from .mixins.transcript import TranscriptMixin


class ShortsFactoryWindow(
    QMainWindow,
    PlaybackMixin,
    TranscriptMixin,
    SettingsMixin,
    AIClipHunterMixin,
    MusicMixin,
    RenderPipelineMixin,
    PersistentTitleMixin,
    EditorAssetsMixin,
    EmojiPreviewMixin,
    CaptionPreviewMixin,
    RecapMixin,
):

    def __init__(self):

        super().__init__()

        self.video_path: Path | None = None

        self.start_ms = 0
        self.end_ms = 0

        self.render_process = QProcess(self)

        self.render_process.setWorkingDirectory(
            str(ROOT)
        )

        # Force UTF-8 for render.py and every Python subprocess it launches.
        # This prevents Windows cp1252 crashes when logs contain emoji or
        # other Unicode characters.
        process_env = QProcessEnvironment.systemEnvironment()
        process_env.insert("PYTHONIOENCODING", "utf-8")
        process_env.insert("PYTHONUTF8", "1")

        # render.py's own stdout is a pipe (not a terminal), so Python
        # defaults to full block-buffering for it -- its own print()
        # "=== STEP N ===" progress headers then sit in that buffer for
        # the entire multi-minute render and only appear all at once right
        # before the process exits, even though the ffmpeg/whisper
        # subprocesses it shells out to stream their own output live (each
        # is short-lived, so its buffer auto-flushes at its own exit).
        # Forcing every Python process this app launches fully unbuffered
        # makes the live render log actually reflect progress in real time.
        process_env.insert("PYTHONUNBUFFERED", "1")

        self.render_process.setProcessEnvironment(
            process_env
        )

        self.render_process.readyReadStandardOutput.connect(
            self.read_render_output
        )

        self.render_process.readyReadStandardError.connect(
            self.read_render_error
        )

        self.render_process.finished.connect(
            self.render_finished
        )

        self.analysis_process = QProcess(self)

        self.analysis_process.setWorkingDirectory(
            str(ROOT)
        )

        self.analysis_process.setProcessEnvironment(
            process_env
        )

        self.analysis_process.readyReadStandardOutput.connect(
            self.read_analysis_output
        )

        self.analysis_process.readyReadStandardError.connect(
            self.read_analysis_error
        )

        self.analysis_process.finished.connect(
            self.analysis_finished
        )

        self.analysis_stage: str | None = None

        # Background source-transcript preparation. This uses the same
        # subtitles.py cache path/quality rules as Find Best Clips, but runs
        # as soon as a source is loaded so later analysis can reuse it.
        self.transcript_preload_process = QProcess(self)
        self.transcript_preload_process.setWorkingDirectory(
            str(ROOT)
        )
        self.transcript_preload_process.setProcessEnvironment(
            process_env
        )
        self.transcript_preload_process.readyReadStandardOutput.connect(
            self.read_transcript_preload_output
        )
        self.transcript_preload_process.readyReadStandardError.connect(
            self.read_transcript_preload_error
        )
        self.transcript_preload_process.finished.connect(
            self.transcript_preload_finished
        )
        self.transcript_preload_source = ""
        self.transcript_preload_quality = ""
        self.transcript_preload_output = ""
        self.pending_find_best_after_preload = False

        self.emoji_preview_process = QProcess(self)

        self.emoji_preview_process.setWorkingDirectory(
            str(ROOT)
        )

        self.emoji_preview_process.setProcessEnvironment(
            process_env
        )

        self.emoji_preview_process.finished.connect(
            self.emoji_preview_plan_finished
        )

        self.emoji_preview_dragging = False
        self.emoji_preview_drag_slot = None
        self.emoji_preview_drag_origin = QPoint()
        self.emoji_preview_drag_start_x = 0.0
        self.emoji_preview_drag_start_y = 0.0
        self.emoji_preview_active: list = []
        self.emoji_preview_labels: list = []

        self.caption_position_x: float | None = None
        self.caption_position_y: float | None = None
        self.caption_scale: float | None = None
        self.caption_preview_dragging = False
        self.caption_preview_drag_origin = QPoint()
        self.caption_preview_drag_start_x = 0.0
        self.caption_preview_drag_start_y = 0.0
        self.youtube_ui_preview_enabled = False

        self.editor_asset_plan: dict = load_editor_asset_plan()
        self.selected_sfx_clip_id: str | None = None
        self.selected_emoji_clip_id: str | None = None
        self.selected_timeline_item_kind: str | None = None
        self.selected_timeline_item_id: str | None = None
        self.sfx_preview_triggered: set[str] = set()

        # Pre-render subject-aware 9:16 framing stage.
        self.reframe_process = QProcess(self)

        self.reframe_process.setWorkingDirectory(
            str(ROOT)
        )

        self.reframe_process.setProcessEnvironment(
            process_env
        )

        self.reframe_process.readyReadStandardOutput.connect(
            self.read_reframe_output
        )

        self.reframe_process.readyReadStandardError.connect(
            self.read_reframe_error
        )

        self.reframe_process.finished.connect(
            self.reframe_finished
        )

        self.pending_render_source: Path | None = None
        self.pending_render_duration_seconds = 0.0
        self.pending_original_start_seconds = 0.0
        self.pending_original_end_seconds = 0.0
        self.render_progress_active = False
        self.render_progress_started_at = 0.0
        self.render_progress_stage_started_at = 0.0
        self.render_progress_estimate_seconds = 0.0
        self.render_progress_stage = "idle"
        self.render_progress_floor = 0
        self.render_progress_ceiling = 100
        self.render_progress_stage_estimate_seconds = 1.0
        self.render_progress_last_value = 0

        self.music_path: Path | None = None
        self.music_volume = 18

        # AI clip candidates currently shown in the editor.
        # Each item stores rank, timing, score, hook, description, and reason.
        self.ai_candidates: list[dict] = []

        self.source_transcript_segments: list[dict] = []

        # Manual transcript cuts are stored using absolute source timing.
        # They survive switching between AI clip candidates for the same source.
        self.manual_cut_segments: set[tuple[int, int]] = set()

        # User-fixed transcript text, keyed by absolute source segment timing.
        self.transcript_corrections: dict[tuple[int, int], str] = {}

        self.current_image_model_title = ""
        self.selected_image_model_title = ""
        self.updating_image_model_combo = False
        self.image_quality = "BALANCED"
        self.updating_visual_inspector = False
        self.updating_timeline_item_inspector = False
        self.user_visual_edits = False
        self.updating_timeline_controls = False
        self.paused_seek_refresh_pending = False
        self.selection_loop_enabled = False
        self.play_request_counter = 0
        self.settings = QSettings(
            "ShortsFactory",
            "ShortsFactory",
        )
        self.preview_volume = int(
            self.settings.value(
                PREVIEW_VOLUME,
                80,
            )
            or 80
        )
        self.preview_volume = max(
            0,
            min(
                100,
                self.preview_volume,
            ),
        )
        self.transcription_quality = str(
            self.settings.value(
                TRANSCRIPTION_QUALITY,
                "AUTO",
            )
            or "AUTO"
        ).upper()
        if self.transcription_quality not in {
            "AUTO",
            "FAST",
            "ACCURATE",
        }:
            self.transcription_quality = "AUTO"

        self.edit_energy = normalize_energy(
            self.settings.value(
                EDIT_ENERGY,
                DEFAULT_ENERGY,
            )
            or DEFAULT_ENERGY
        )

        # QSettings can hand back a string ("true"/"false") instead of a
        # real bool depending on platform backend -- coerce defensively
        # rather than trusting the stored type.
        self.auto_cuts_enabled = str(
            self.settings.value(
                AUTO_CUTS_ENABLED,
                True,
            )
        ).strip().lower() not in ("false", "0", "")

        self.filters_enabled = str(
            self.settings.value(
                FILTERS_ENABLED,
                True,
            )
        ).strip().lower() not in ("false", "0", "")

        self.emoji_enabled = str(
            self.settings.value(
                EMOJI_ENABLED,
                True,
            )
        ).strip().lower() not in ("false", "0", "")

        self.recap_voice = str(
            self.settings.value(
                RECAP_VOICE,
                DEFAULT_ORPHEUS_VOICE,
            )
            or DEFAULT_ORPHEUS_VOICE
        )
        try:
            self.recap_target_duration_seconds = max(
                10,
                int(
                    self.settings.value(
                        RECAP_TARGET_DURATION_SECONDS,
                        120,
                    )
                ),
            )
        except (TypeError, ValueError):
            self.recap_target_duration_seconds = 120
        self.recap_script_source = str(
            self.settings.value(RECAP_SCRIPT_SOURCE, "local") or "local"
        ).strip().lower()
        if self.recap_script_source not in {"local", "external"}:
            self.recap_script_source = "local"
        try:
            self.recap_speed = float(self.settings.value(RECAP_SPEED, 1.5) or 1.5)
        except (TypeError, ValueError):
            self.recap_speed = 1.5
        if self.recap_speed not in {1.25, 1.5, 1.75}:
            self.recap_speed = 1.5
        try:
            self.recap_narration_pitch_semitones = float(
                self.settings.value(
                    RECAP_NARRATION_PITCH_SEMITONES,
                    DEFAULT_NARRATION_PITCH_SEMITONES,
                )
                or DEFAULT_NARRATION_PITCH_SEMITONES
            )
        except (TypeError, ValueError):
            self.recap_narration_pitch_semitones = DEFAULT_NARRATION_PITCH_SEMITONES
        pitch_low, pitch_high = NARRATION_PITCH_SEMITONES_RANGE
        self.recap_narration_pitch_semitones = max(
            pitch_low,
            min(pitch_high, self.recap_narration_pitch_semitones),
        )
        try:
            self.recap_source_pitch_semitones = float(
                self.settings.value(
                    RECAP_SOURCE_PITCH_SEMITONES,
                    DEFAULT_SOURCE_PITCH_SEMITONES,
                )
                or DEFAULT_SOURCE_PITCH_SEMITONES
            )
        except (TypeError, ValueError):
            self.recap_source_pitch_semitones = DEFAULT_SOURCE_PITCH_SEMITONES
        self.recap_source_pitch_semitones = max(
            pitch_low,
            min(pitch_high, self.recap_source_pitch_semitones),
        )
        self.recap_active_script: dict | None = None
        self.recap_active_inputs = None
        self.recap_artifact_context = None
        self.recap_external_script_path: Path | None = None
        self.recap_script_valid = False
        self.recap_sequence = None

        try:
            self.min_emoji_events = max(
                0,
                min(
                    10,
                    int(
                        self.settings.value(
                            MIN_EMOJI_EVENTS,
                            0,
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            self.min_emoji_events = 0
        try:
            self.fx_intensity = min(
                2.0,
                max(
                    0.0,
                    float(
                        self.settings.value(
                            FX_INTENSITY,
                            1.0,
                        )
                        or 1.0
                    ),
                ),
            )
        except (TypeError, ValueError):
            self.fx_intensity = 1.0

        self.sfx_mode = normalize_sfx_mode(
            self.settings.value(
                SFX_MODE,
                "AUTO",
            )
            or "AUTO"
        )

        self.music_process = QProcess(self)

        self.music_process.setWorkingDirectory(
            str(ROOT)
        )

        self.music_process.setProcessEnvironment(
            process_env
        )

        self.music_process.readyReadStandardOutput.connect(
            self.read_music_output
        )

        self.music_process.readyReadStandardError.connect(
            self.read_music_error
        )

        self.music_process.finished.connect(
            self.music_finished
        )

        self.sfx_process = QProcess(self)

        self.sfx_process.setWorkingDirectory(
            str(ROOT)
        )

        self.sfx_process.setProcessEnvironment(
            process_env
        )

        self.sfx_process.readyReadStandardOutput.connect(
            self.read_sfx_output
        )

        self.sfx_process.readyReadStandardError.connect(
            self.read_sfx_error
        )

        self.sfx_process.finished.connect(
            self.sfx_finished
        )

        self.emoji_generate_process = QProcess(self)

        self.emoji_generate_process.setWorkingDirectory(
            str(ROOT)
        )

        self.emoji_generate_process.setProcessEnvironment(
            process_env
        )

        self.emoji_generate_process.readyReadStandardOutput.connect(
            self.read_emoji_generate_output
        )

        self.emoji_generate_process.readyReadStandardError.connect(
            self.read_emoji_generate_error
        )

        self.emoji_generate_process.finished.connect(
            self.emoji_generate_finished
        )

        self.render_progress_timer = QTimer(
            self
        )
        self.render_progress_timer.setInterval(
            500
        )
        self.render_progress_timer.timeout.connect(
            self.update_render_progress
        )

        # Always-on footer activity polling. Render progress remains
        # determinate; every other QProcess-backed generation task is shown
        # as an indeterminate busy bar so the user never has to scroll to see
        # whether ShortsFactory is still working.
        self.global_progress_timer = QTimer(
            self
        )
        self.global_progress_timer.setInterval(
            250
        )
        self.global_progress_timer.timeout.connect(
            self.update_global_progress
        )

        self.setWindowTitle(
            "ShortsFactory"
        )

        self.resize(
            1400,
            1040,
        )

        self.setMinimumSize(
            1120,
            720,
        )

        self.audio_output = QAudioOutput()

        self.audio_output.setVolume(
            self.preview_volume
            / 100
        )

        self.player = QMediaPlayer()

        self.player.setAudioOutput(
            self.audio_output
        )

        self.player.positionChanged.connect(
            self.position_changed
        )

        self.player.durationChanged.connect(
            self.duration_changed
        )

        self.player.playbackStateChanged.connect(
            self.update_play_button
        )

        self.player.mediaStatusChanged.connect(
            self.media_status_changed
        )

        self.player.errorOccurred.connect(
            self.playback_error_occurred
        )

        self.sfx_preview_audio = QAudioOutput()
        self.sfx_preview_player = QMediaPlayer()
        self.sfx_preview_player.setAudioOutput(
            self.sfx_preview_audio
        )

        self.build_ui()
        self.apply_style()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(
                self
            )
        QTimer.singleShot(
            0,
            self.restore_layout_settings,
        )
        self.load_editor_asset_plan_state()
        self.global_progress_timer.start()
        self.update_global_progress()

    def build_ui(self):

        root = QWidget()
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(18, 16, 18, 18)
        main_layout.setSpacing(14)

        # ====================================================
        # HEADER
        # ====================================================

        header_frame = QFrame()
        header_frame.setObjectName("HeaderPanel")

        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(12)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(2)

        title = QLabel("ShortsFactory")
        title.setObjectName("AppTitle")

        subtitle = QLabel("SLAUGHTERHOUSE EDIT SYSTEM  //  CONTENT PROCESSING / CUT FLOOR")
        subtitle.setObjectName("AppSubtitle")

        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)

        mode_badge = QLabel("CUT FLOOR ARMED")
        mode_badge.setObjectName("ModeBadge")
        mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_layout.addLayout(title_stack, 1)
        header_layout.addWidget(mode_badge)

        self.generate_button = QPushButton("Render Final Video")
        self.generate_button.setObjectName("GenerateButton")
        self.generate_button.setEnabled(False)
        self.generate_button.setToolTip("Select a clip first.")
        self.generate_button.clicked.connect(self.generate_short)
        header_layout.addWidget(self.generate_button)

        main_layout.addWidget(header_frame)

        # ====================================================
        # WORKSPACE
        # ====================================================

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setObjectName("MainSplitter")
        workspace.setChildrenCollapsible(False)
        self.main_splitter = workspace
        self.workspace_splitter = workspace

        # ----------------------------------------------------
        # LEFT RAIL / SOURCE PANEL
        # ----------------------------------------------------

        source_frame = QFrame()
        source_frame.setObjectName("Panel")
        # The source rail needs a stable working width, but it should not
        # absorb the space the editor needs on larger desktop displays.
        source_frame.setMinimumWidth(320)
        source_frame.setMaximumWidth(440)

        source_layout = QVBoxLayout(source_frame)
        source_layout.setContentsMargins(16, 16, 16, 16)
        source_layout.setSpacing(12)
        self.source_layout = source_layout

        left_title = QLabel("SOURCE FEED")
        left_title.setObjectName("SectionTitle")

        self.drop_zone = DropZone(self.load_video)
        self.drop_zone.setMinimumHeight(360)
        self.drop_zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.file_label = QLabel("No video loaded")
        self.file_label.setObjectName("FileLabel")
        self.file_label.setWordWrap(True)

        source_hint = QLabel("Drop a source clip, podcast, movie scene, or episode segment here to begin scouting Shorts moments.")
        source_hint.setObjectName("HintLabel")
        source_hint.setWordWrap(True)

        transcription_label = QLabel("TRANSCRIPTION")
        transcription_label.setObjectName("TinyLabel")

        self.transcription_quality_combo = QComboBox()
        self.transcription_quality_combo.setObjectName("CompactCombo")
        self.transcription_quality_combo.addItems(
            [
                "AUTO",
                "FAST",
                "ACCURATE",
            ]
        )
        self.transcription_quality_combo.setCurrentText(
            self.transcription_quality
        )
        self.transcription_quality_combo.setToolTip(
            "Choose local transcription quality for AI Clip Hunter."
        )
        self.transcription_quality_combo.currentTextChanged.connect(
            self.transcription_quality_changed
        )

        transcription_row = QHBoxLayout()
        transcription_row.setSpacing(8)
        transcription_row.addWidget(transcription_label)
        transcription_row.addStretch()
        transcription_row.addWidget(
            self.transcription_quality_combo
        )

        self.find_clips_button = QPushButton("✦ Find Best Clips")
        self.find_clips_button.setObjectName("AIButton")
        self.find_clips_button.setToolTip("Transcribe the source and highlight AI-ranked Short candidates.")
        self.find_clips_button.setEnabled(False)
        self.find_clips_button.clicked.connect(self.find_best_clips)

        edit_style_frame = QFrame()
        edit_style_frame.setObjectName("EditStylePanel")
        edit_style_layout = QVBoxLayout(edit_style_frame)
        edit_style_layout.setContentsMargins(10, 9, 10, 10)
        edit_style_layout.setSpacing(7)

        edit_style_label = QLabel("EDIT STYLE")
        edit_style_label.setObjectName("TinyLabel")
        edit_style_layout.addWidget(edit_style_label)

        self.auto_cuts_button = QPushButton(
            "AUTO CUTS: ON" if self.auto_cuts_enabled else "AUTO CUTS: OFF"
        )
        self.auto_cuts_button.setObjectName("AutoCutsToggle")
        self.auto_cuts_button.setCheckable(True)
        self.auto_cuts_button.setChecked(self.auto_cuts_enabled)
        self.auto_cuts_button.setToolTip(
            "Removes dead air, silence, and redundant speech at render "
            "time. Turn off to render the clip exactly as trimmed, full "
            "length -- only your own manual cuts still apply."
        )
        self.auto_cuts_button.clicked.connect(self.auto_cuts_toggled)
        edit_style_layout.addWidget(self.auto_cuts_button)

        auto_cuts_subtext = QLabel(
            "Removes dead air, silence, and redundant speech. Turn off "
            "to keep the clip exactly as trimmed."
        )
        auto_cuts_subtext.setObjectName("HintLabel")
        auto_cuts_subtext.setWordWrap(True)
        edit_style_layout.addWidget(auto_cuts_subtext)

        edit_style_buttons = QVBoxLayout()
        edit_style_buttons.setSpacing(6)

        self.edit_style_group = QButtonGroup(self)
        self.edit_style_group.setExclusive(True)
        self.edit_style_buttons: dict[str, QPushButton] = {}

        style_options = (
            (
                "LOW",
                "LOW\nCLEAN",
                "Cleaner, restrained editing with lighter motion, captions, FX, and SFX.",
            ),
            (
                "PUNCHY",
                "PUNCHY\nVIRAL",
                "Fast viral Shorts pacing with balanced motion, captions, FX, and SFX.",
            ),
            (
                "MAXIMUM",
                "MAXIMUM\nHEAVY",
                "Aggressive editing with the strongest motion, captions, FX, and SFX density.",
            ),
        )

        for energy, button_text, tooltip in style_options:
            button = QPushButton(button_text)
            button.setObjectName("EditStyleButton")
            button.setCheckable(True)
            button.setChecked(energy == self.edit_energy)
            button.setToolTip(tooltip)
            button.clicked.connect(
                lambda checked=False, value=energy: self.edit_energy_changed(value)
            )
            self.edit_style_group.addButton(button)
            self.edit_style_buttons[energy] = button
            edit_style_buttons.addWidget(button)

        edit_style_layout.addLayout(edit_style_buttons)

        self.filters_button = QPushButton(
            "FILTERS: ON" if self.filters_enabled else "FILTERS: OFF"
        )
        self.filters_button.setObjectName("FiltersToggle")
        self.filters_button.setCheckable(True)
        self.filters_button.setChecked(self.filters_enabled)
        self.filters_button.setToolTip(
            "Turns off color grading, vignette, and filter/graphic accents "
            "for this render. Turn off for an unfiltered, natural-looking "
            "export."
        )
        self.filters_button.clicked.connect(self.filters_toggled)
        edit_style_layout.addWidget(self.filters_button)

        filters_subtext = QLabel(
            "Turns off color grading, vignette, and filter/graphic accents. "
            "Smart motion (camera punch-ins) is unaffected."
        )
        filters_subtext.setObjectName("HintLabel")
        filters_subtext.setWordWrap(True)
        edit_style_layout.addWidget(filters_subtext)

        fx_intensity_row = QVBoxLayout()
        fx_intensity_row.setSpacing(8)

        fx_intensity_header = QHBoxLayout()
        fx_intensity_header.setSpacing(8)

        self.fx_intensity_title = QLabel("FILTER INTENSITY")
        self.fx_intensity_title.setObjectName("TinyLabel")
        self.fx_intensity_title.setEnabled(self.filters_enabled)

        self.fx_intensity_label = QLabel(
            f"{round(self.fx_intensity * 100)}%"
        )
        self.fx_intensity_label.setObjectName("MusicVolumeLabel")
        self.fx_intensity_label.setEnabled(self.filters_enabled)

        self.fx_intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self.fx_intensity_slider.setObjectName("MusicVolumeSlider")
        self.fx_intensity_slider.setRange(0, 200)
        self.fx_intensity_slider.setValue(round(self.fx_intensity * 100))
        self.fx_intensity_slider.setToolTip(
            "Scales the color grade/vignette strength for the selected edit "
            "style. 100% is the style's normal look; 0% disables it."
        )
        self.fx_intensity_slider.valueChanged.connect(self.fx_intensity_changed)
        self.fx_intensity_slider.setEnabled(self.filters_enabled)

        fx_intensity_header.addWidget(self.fx_intensity_title)
        fx_intensity_header.addStretch()
        fx_intensity_header.addWidget(self.fx_intensity_label)
        fx_intensity_row.addLayout(fx_intensity_header)
        fx_intensity_row.addWidget(self.fx_intensity_slider)

        edit_style_layout.addLayout(fx_intensity_row)

        self.recap_button = QPushButton("AI RECAP")
        self.recap_button.setObjectName("QuietButton")
        self.recap_button.setCheckable(True)
        self.recap_button.setToolTip(
            "AI Recap Mode: turn a full episode into a narrated recap "
            "short. Requires Track A's research/story files under "
            "output/recap/ (episode_identity.json, verified_story_map.json, "
            "recap_script.json)."
        )
        self.recap_button.clicked.connect(self.toggle_recap_panel)

        self.standard_short_button = QPushButton("STANDARD")
        self.standard_short_button.setObjectName("QuietButton")
        self.standard_short_button.setCheckable(True)
        self.standard_short_button.setChecked(True)
        self.standard_short_button.setToolTip("Use the normal ShortsFactory workflow.")
        self.standard_short_button.clicked.connect(self.set_standard_short_mode)

        self.recap_mode_buttons = QButtonGroup(self)
        self.recap_mode_buttons.setExclusive(True)
        self.recap_mode_buttons.addButton(self.standard_short_button)
        self.recap_mode_buttons.addButton(self.recap_button)

        recap_mode_row = QHBoxLayout()
        recap_mode_row.setSpacing(6)
        recap_mode_row.addWidget(self.standard_short_button)
        recap_mode_row.addWidget(self.recap_button)

        self.recap_frame = QFrame()
        self.recap_frame.setObjectName("EditStylePanel")
        self.recap_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        recap_layout = QVBoxLayout(self.recap_frame)
        recap_layout.setContentsMargins(10, 9, 10, 10)
        recap_layout.setSpacing(7)

        recap_title = QLabel("AI RECAP")
        recap_title.setObjectName("TinyLabel")
        recap_layout.addWidget(recap_title)

        recap_source_row = QHBoxLayout()
        recap_source_row.setSpacing(6)
        self.recap_source_label = QLabel("Source: current input source")
        self.recap_source_label.setObjectName("HintLabel")
        self.recap_source_label.setWordWrap(True)
        self.recap_browse_source_button = QPushButton("Browse")
        self.recap_browse_source_button.setObjectName("QuietButton")
        self.recap_browse_source_button.setToolTip("Choose the episode source for the existing app.")
        self.recap_browse_source_button.clicked.connect(self.drop_zone.browse_file)
        recap_source_row.addWidget(self.recap_source_label, 1)
        recap_source_row.addWidget(self.recap_browse_source_button)

        self.recap_episode_label = QLabel("Episode: not checked")
        self.recap_episode_label.setObjectName("HintLabel")
        self.recap_episode_label.setWordWrap(True)
        recap_layout.addWidget(self.recap_episode_label)

        recap_script_source_row = QVBoxLayout()
        recap_script_source_row.setSpacing(6)
        recap_script_source_label = QLabel("SCRIPT SOURCE")
        recap_script_source_label.setObjectName("TinyLabel")
        self.recap_script_source_combo = QComboBox()
        self.recap_script_source_combo.setObjectName("CompactCombo")
        self.recap_script_source_combo.addItems(
            ["Local AI", "Import AI Script", "Paste Script"]
        )
        self.recap_script_source_combo.setCurrentText(
            "Import AI Script" if self.recap_script_source == "external" else "Local AI"
        )
        self.recap_script_source_combo.currentTextChanged.connect(
            self.recap_script_source_changed
        )
        recap_script_source_row.addWidget(recap_script_source_label)
        recap_script_source_row.addWidget(self.recap_script_source_combo)
        recap_layout.addLayout(recap_script_source_row)

        recap_import_row = QVBoxLayout()
        recap_import_row.setSpacing(6)
        self.recap_import_script_button = QPushButton("Import JSON")
        self.recap_import_script_button.setObjectName("QuietButton")
        self.recap_import_script_button.clicked.connect(self.choose_external_recap_script)
        self.recap_paste_script_button = QPushButton("Paste JSON")
        self.recap_paste_script_button.setObjectName("QuietButton")
        self.recap_paste_script_button.clicked.connect(self.open_recap_paste_dialog)
        recap_import_row.addWidget(self.recap_import_script_button)
        recap_import_row.addWidget(self.recap_paste_script_button)
        recap_layout.addLayout(recap_import_row)

        self.recap_status_label = QLabel("Recap Intelligence: not checked.")
        self.recap_status_label.setObjectName("RecapStatus")
        self.recap_status_label.setWordWrap(True)
        recap_layout.addWidget(self.recap_status_label)

        recap_duration_row = QVBoxLayout()
        recap_duration_row.setSpacing(8)
        recap_duration_label = QLabel("TARGET DURATION (s)")
        recap_duration_label.setObjectName("TinyLabel")
        self.recap_duration_spinbox = QSpinBox()
        self.recap_duration_spinbox.setObjectName("CompactSpinBox")
        self.recap_duration_spinbox.setRange(10, 600)
        self.recap_duration_spinbox.setValue(self.recap_target_duration_seconds)
        self.recap_duration_spinbox.setToolTip(
            "Preferred recap length -- a stored preference. Track A's own "
            "recap_script.json target is reported after generating the "
            "sequence."
        )
        self.recap_duration_spinbox.valueChanged.connect(
            self.recap_target_duration_changed
        )
        recap_duration_row.addWidget(recap_duration_label)
        recap_duration_row.addWidget(self.recap_duration_spinbox)
        recap_layout.addLayout(recap_duration_row)

        recap_voice_row = QHBoxLayout()
        recap_voice_row.setSpacing(8)
        recap_voice_label = QLabel("VOICE")
        recap_voice_label.setObjectName("TinyLabel")
        self.recap_voice_combo = QComboBox()
        self.recap_voice_combo.setObjectName("CompactCombo")
        self.recap_voice_combo.addItem(self.recap_voice)
        self.recap_voice_combo.setToolTip(
            "Orpheus-FastAPI voice used for narration. Refresh to query "
            "the local Orpheus server; falls back to the known default "
            "voice set when it isn't reachable."
        )
        self.recap_voice_combo.currentTextChanged.connect(self.recap_voice_changed)
        self.recap_refresh_voices_button = QPushButton("⟳")
        self.recap_refresh_voices_button.setObjectName("TinyButton")
        self.recap_refresh_voices_button.setToolTip(
            "Refresh the voice list from Orpheus-FastAPI."
        )
        self.recap_refresh_voices_button.clicked.connect(self.refresh_recap_voices)
        recap_voice_row.addWidget(recap_voice_label)
        recap_voice_row.addWidget(self.recap_voice_combo, 1)
        recap_voice_row.addWidget(self.recap_refresh_voices_button)
        recap_layout.addLayout(recap_voice_row)

        recap_speed_row = QHBoxLayout()
        recap_speed_row.setSpacing(8)
        recap_speed_label = QLabel("OVERALL SPEED")
        recap_speed_label.setObjectName("TinyLabel")
        self.recap_speed_combo = QComboBox()
        self.recap_speed_combo.setObjectName("CompactCombo")
        self.recap_speed_combo.addItems(["1.25x", "1.50x", "1.75x"])
        self.recap_speed_combo.setCurrentText(f"{self.recap_speed:.2f}x")
        self.recap_speed_combo.currentTextChanged.connect(self.recap_speed_changed)
        recap_speed_row.addWidget(recap_speed_label)
        recap_speed_row.addWidget(self.recap_speed_combo, 1)
        recap_layout.addLayout(recap_speed_row)

        recap_pitch_row = QHBoxLayout()
        recap_pitch_row.setSpacing(8)
        recap_pitch_label = QLabel("NARRATION PITCH")
        recap_pitch_label.setObjectName("TinyLabel")
        self.recap_narration_pitch_spinbox = QDoubleSpinBox()
        self.recap_narration_pitch_spinbox.setObjectName("CompactSpinBox")
        self.recap_narration_pitch_spinbox.setRange(*NARRATION_PITCH_SEMITONES_RANGE)
        self.recap_narration_pitch_spinbox.setSingleStep(0.1)
        self.recap_narration_pitch_spinbox.setDecimals(1)
        self.recap_narration_pitch_spinbox.setSuffix(" st")
        self.recap_narration_pitch_spinbox.setValue(self.recap_narration_pitch_semitones)
        self.recap_narration_pitch_spinbox.setToolTip(
            "Pitch shift applied only during recap rendering; cached narration WAVs remain unchanged."
        )
        self.recap_narration_pitch_spinbox.valueChanged.connect(
            self.recap_narration_pitch_changed
        )
        recap_pitch_row.addWidget(recap_pitch_label)
        recap_pitch_row.addWidget(self.recap_narration_pitch_spinbox, 1)
        recap_layout.addLayout(recap_pitch_row)

        recap_source_pitch_row = QHBoxLayout()
        recap_source_pitch_row.setSpacing(8)
        recap_source_pitch_label = QLabel("SOURCE PITCH")
        recap_source_pitch_label.setObjectName("TinyLabel")
        self.recap_source_pitch_spinbox = QDoubleSpinBox()
        self.recap_source_pitch_spinbox.setObjectName("CompactSpinBox")
        self.recap_source_pitch_spinbox.setRange(*NARRATION_PITCH_SEMITONES_RANGE)
        self.recap_source_pitch_spinbox.setSingleStep(0.1)
        self.recap_source_pitch_spinbox.setDecimals(1)
        self.recap_source_pitch_spinbox.setSuffix(" st")
        self.recap_source_pitch_spinbox.setValue(self.recap_source_pitch_semitones)
        self.recap_source_pitch_spinbox.setToolTip(
            "Duration-preserving pitch shift for audible episode audio during recap rendering."
        )
        self.recap_source_pitch_spinbox.valueChanged.connect(self.recap_source_pitch_changed)
        recap_source_pitch_row.addWidget(recap_source_pitch_label)
        recap_source_pitch_row.addWidget(self.recap_source_pitch_spinbox, 1)
        recap_layout.addLayout(recap_source_pitch_row)

        self.recap_script_preview = QListWidget()
        self.recap_script_preview.setObjectName("TranscriptList")
        self.recap_script_preview.setFixedHeight(150)
        self.recap_script_preview.setToolTip(
            "Ordered validated recap blocks. Narration text is shown exactly as supplied."
        )
        recap_layout.addWidget(self.recap_script_preview)

        recap_actions_row = QVBoxLayout()
        recap_actions_row.setSpacing(6)
        self.validate_recap_script_button = QPushButton("Validate Script")
        self.validate_recap_script_button.setObjectName("QuietButton")
        self.validate_recap_script_button.clicked.connect(self.validate_active_recap_script)

        self.generate_recap_sequence_button = QPushButton("Generate Recap")
        self.generate_recap_sequence_button.setObjectName("QuietButton")
        self.generate_recap_sequence_button.setEnabled(False)
        self.generate_recap_sequence_button.setToolTip(
            "Assemble the recap sequence and synthesize narration through the local Orpheus server."
        )
        self.generate_recap_sequence_button.clicked.connect(
            self.generate_recap
        )

        self.generate_recap_voiceover_button = QPushButton("Generate Voiceover")
        self.generate_recap_voiceover_button.setObjectName("QuietButton")
        self.generate_recap_voiceover_button.setEnabled(False)
        self.generate_recap_voiceover_button.setToolTip(
            "Synthesize narration for every segment via the local "
            "Orpheus-FastAPI server and place them on the VOICEOVER "
            "timeline lane."
        )
        self.generate_recap_voiceover_button.clicked.connect(
            self.generate_recap_voiceover
        )

        recap_actions_row.addWidget(self.validate_recap_script_button)
        recap_actions_row.addWidget(self.generate_recap_sequence_button)
        recap_layout.addLayout(recap_actions_row)

        recap_followup_actions_row = QVBoxLayout()
        recap_followup_actions_row.setSpacing(6)
        self.recap_open_editor_button = QPushButton("Open in Editor")
        self.recap_open_editor_button.setObjectName("QuietButton")
        self.recap_open_editor_button.setEnabled(False)
        self.recap_open_editor_button.clicked.connect(self.open_recap_in_editor)
        recap_followup_actions_row.addWidget(self.generate_recap_voiceover_button)
        recap_followup_actions_row.addWidget(self.recap_open_editor_button)
        recap_layout.addLayout(recap_followup_actions_row)

        self.recap_voiceover_list = QListWidget()
        self.recap_voiceover_list.setObjectName("TranscriptList")
        self.recap_voiceover_list.setFixedHeight(110)
        self.recap_voiceover_list.setToolTip(
            "Narration segments placed on the VOICEOVER timeline lane."
        )
        self.recap_voiceover_list.currentRowChanged.connect(
            self.update_recap_voiceover_context
        )
        recap_layout.addWidget(self.recap_voiceover_list)

        recap_context_row = QVBoxLayout()
        recap_context_row.setSpacing(6)

        recap_context_volume_row = QHBoxLayout()
        recap_context_volume_row.setSpacing(6)

        self.recap_voiceover_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.recap_voiceover_volume_slider.setObjectName("MusicVolumeSlider")
        self.recap_voiceover_volume_slider.setRange(0, 100)
        self.recap_voiceover_volume_slider.setValue(100)
        self.recap_voiceover_volume_slider.setEnabled(False)
        self.recap_voiceover_volume_slider.setToolTip(
            "Selected narration segment's volume."
        )
        self.recap_voiceover_volume_slider.valueChanged.connect(
            self.recap_voiceover_volume_changed
        )

        self.recap_voiceover_toggle_button = QPushButton("Disable")
        self.recap_voiceover_toggle_button.setObjectName("QuietButton")
        self.recap_voiceover_toggle_button.setEnabled(False)
        self.recap_voiceover_toggle_button.clicked.connect(
            self.toggle_selected_recap_voiceover_clip
        )

        self.recap_voiceover_regenerate_button = QPushButton("Regenerate")
        self.recap_voiceover_regenerate_button.setObjectName("QuietButton")
        self.recap_voiceover_regenerate_button.setEnabled(False)
        self.recap_voiceover_regenerate_button.clicked.connect(
            self.regenerate_selected_recap_voiceover_clip
        )

        self.recap_voiceover_delete_button = QPushButton("Delete")
        self.recap_voiceover_delete_button.setObjectName("CutButton")
        self.recap_voiceover_delete_button.setEnabled(False)
        self.recap_voiceover_delete_button.clicked.connect(
            self.delete_selected_recap_voiceover_clip
        )

        recap_context_volume_row.addWidget(self.recap_voiceover_volume_slider, 1)
        recap_context_volume_row.addWidget(self.recap_voiceover_toggle_button)

        recap_context_actions_row = QVBoxLayout()
        recap_context_actions_row.setSpacing(6)
        recap_context_actions_row.addWidget(self.recap_voiceover_regenerate_button)
        recap_context_actions_row.addWidget(self.recap_voiceover_delete_button)

        recap_context_row.addLayout(recap_context_volume_row)
        recap_context_row.addLayout(recap_context_actions_row)
        recap_layout.addLayout(recap_context_row)

        for control in (
            self.recap_browse_source_button,
            self.recap_script_source_combo,
            self.recap_import_script_button,
            self.recap_paste_script_button,
            self.recap_duration_spinbox,
            self.recap_voice_combo,
            self.recap_speed_combo,
            self.recap_narration_pitch_spinbox,
            self.recap_source_pitch_spinbox,
            self.validate_recap_script_button,
            self.generate_recap_sequence_button,
            self.generate_recap_voiceover_button,
            self.recap_open_editor_button,
            self.recap_voiceover_toggle_button,
            self.recap_voiceover_regenerate_button,
            self.recap_voiceover_delete_button,
        ):
            control.setMinimumWidth(0)
            control.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )

        self.recap_log = QTextEdit()
        self.recap_log.setReadOnly(True)
        self.recap_log.setFixedHeight(90)
        self.recap_log.setPlaceholderText("Recap generation log will appear here...")
        recap_layout.addWidget(self.recap_log)

        self.standard_short_controls_frame = QWidget()
        self.standard_short_controls_frame.setMinimumWidth(0)
        self.standard_short_controls_frame.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        standard_short_mode_layout = QVBoxLayout(self.standard_short_controls_frame)
        standard_short_mode_layout.setContentsMargins(0, 0, 0, 0)
        standard_short_mode_layout.setSpacing(12)
        standard_short_mode_layout.addWidget(self.find_clips_button)
        standard_short_mode_layout.addWidget(edit_style_frame)
        standard_short_mode_layout.addStretch()

        edit_style_frame.setMinimumWidth(0)
        edit_style_frame.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        for control in (
            self.find_clips_button,
            self.auto_cuts_button,
            *self.edit_style_buttons.values(),
            self.filters_button,
            self.fx_intensity_slider,
        ):
            control.setMinimumWidth(0)
            control.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )

        self.standard_short_mode_frame = QScrollArea()
        self.standard_short_mode_frame.setObjectName("PanelScroll")
        self.standard_short_mode_frame.setWidgetResizable(True)
        self.standard_short_mode_frame.setFrameShape(QFrame.Shape.NoFrame)
        self.standard_short_mode_frame.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.standard_short_mode_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.standard_short_mode_frame.setWidget(self.standard_short_controls_frame)

        self.recap_scroll_area = QScrollArea()
        self.recap_scroll_area.setObjectName("PanelScroll")
        self.recap_scroll_area.setWidgetResizable(True)
        self.recap_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.recap_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.recap_scroll_area.setWidget(self.recap_frame)

        self.mode_specific_stack = QStackedWidget()
        self.mode_specific_stack.addWidget(self.standard_short_mode_frame)
        self.mode_specific_stack.addWidget(self.recap_scroll_area)
        self.mode_specific_stack.setCurrentWidget(self.standard_short_mode_frame)

        source_layout.addWidget(left_title)
        source_layout.addWidget(self.drop_zone, 1)
        source_layout.addWidget(self.file_label)
        source_layout.addWidget(source_hint)
        source_layout.addLayout(transcription_row)
        source_layout.addSpacing(6)
        source_layout.addLayout(recap_mode_row)
        source_layout.addWidget(self.mode_specific_stack, 1)

        workspace.addWidget(source_frame)

        # ----------------------------------------------------
        # CENTER / PREVIEW PANEL
        # ----------------------------------------------------

        center_widget = QWidget()
        center_widget.setObjectName("CenterColumn")
        # The portrait program monitor is height-limited. Keeping the
        # surrounding column bounded prevents oversized black gutters and
        # leaves usable horizontal room for the timeline/editor column.
        center_widget.setMinimumWidth(380)

        center_scroll = QScrollArea()
        center_scroll.setObjectName("CenterScroll")
        center_scroll.setWidgetResizable(True)
        center_scroll.setFrameShape(QFrame.Shape.NoFrame)
        center_scroll.setMinimumWidth(380)
        center_scroll.setMaximumWidth(700)
        center_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        center_scroll.setWidget(center_widget)
        self.center_scroll = center_scroll

        center_column = QVBoxLayout(center_widget)
        center_column.setContentsMargins(0, 0, 0, 0)
        center_column.setSpacing(14)

        center_editor_stack = QWidget()
        center_editor_stack.setObjectName("CenterEditorStack")
        center_editor_layout = QVBoxLayout(center_editor_stack)
        center_editor_layout.setContentsMargins(0, 0, 0, 0)
        center_editor_layout.setSpacing(14)

        preview_frame = QFrame()
        preview_frame.setObjectName("PreviewPanel")

        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(12, 12, 12, 10)
        preview_layout.setSpacing(6)

        preview_header = QHBoxLayout()
        preview_header.setSpacing(10)

        preview_title = QLabel("PROGRAM MONITOR")
        preview_title.setObjectName("SectionTitle")

        preview_tag = QLabel("9:16 EDIT PREVIEW")
        preview_tag.setObjectName("MicroBadge")

        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        preview_header.addWidget(preview_tag)

        self.program_monitor_viewport = AspectRatioContainer(9, 16)
        self.program_monitor_viewport.setObjectName("ProgramMonitorViewport")

        self.program_monitor_composition = ProgramMonitorComposition()
        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("VideoPreview")
        self.program_monitor_composition.set_foreground_video(self.video_widget)
        self.program_monitor_viewport.set_content(self.program_monitor_composition)

        self.player.setVideoOutput(self.video_widget)
        video_sink = self.video_widget.videoSink()
        if video_sink is not None:
            video_sink.videoFrameChanged.connect(
                self.program_monitor_composition.set_background_frame
            )

        playback = QHBoxLayout()
        playback.setSpacing(8)

        self.play_button = QPushButton("PLAY")
        self.play_button.setObjectName("PlayButton")
        self.play_button.setToolTip("Play or pause the preview. Shortcut: Space")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.toggle_playback)

        self.current_time_label = QLabel("00:00.000")
        self.current_time_label.setObjectName("TimeLabel")

        self.duration_label = QLabel("00:00.000")
        self.duration_label.setObjectName("TimeLabel")

        preview_volume_text = QLabel("Preview")
        preview_volume_text.setObjectName("MicroLabel")

        self.preview_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.preview_volume_slider.setObjectName("PreviewVolumeSlider")
        self.preview_volume_slider.setRange(0, 100)
        self.preview_volume_slider.setValue(self.preview_volume)
        self.preview_volume_slider.setFixedWidth(104)
        self.preview_volume_slider.setToolTip(
            "Editor preview volume only. This does not change rendered output volume."
        )
        self.preview_volume_slider.valueChanged.connect(
            self.preview_volume_changed
        )

        self.preview_volume_label = QLabel(
            f"{self.preview_volume}%"
        )
        self.preview_volume_label.setObjectName("MusicVolumeLabel")

        playback.addWidget(self.play_button)
        playback.addWidget(self.current_time_label)
        playback.addWidget(QLabel("/"))
        playback.addWidget(self.duration_label)
        playback.addStretch()
        playback.addWidget(preview_volume_text)
        playback.addWidget(self.preview_volume_slider)
        playback.addWidget(self.preview_volume_label)

        title_controls = QHBoxLayout()
        title_controls.setSpacing(8)
        title_label = QLabel("VIDEO TITLE")
        title_label.setObjectName("TinyLabel")
        self.persistent_title_input = QLineEdit()
        self.persistent_title_input.setObjectName("PersistentTitleInput")
        self.persistent_title_input.setPlaceholderText("Add a persistent title for the exported video")
        self.persistent_title_input.setMaxLength(180)
        self.persistent_title_input.setToolTip(
            "A persistent title rendered above the foreground footage. "
            "It is separate from captions."
        )
        self.persistent_title_input.textChanged.connect(
            self.persistent_video_title_changed,
        )
        self.youtube_ui_preview_toggle = QCheckBox("UI Preview")
        self.youtube_ui_preview_toggle.setObjectName("UiPreviewToggle")
        self.youtube_ui_preview_toggle.setToolTip(
            "Show or hide the preview-only YouTube Shorts mobile UI. "
            "It is never exported."
        )
        self.youtube_ui_preview_toggle.toggled.connect(
            self.set_youtube_ui_preview_enabled,
        )
        title_controls.addWidget(title_label)
        title_controls.addWidget(self.persistent_title_input, 1)
        title_controls.addWidget(self.youtube_ui_preview_toggle)

        self.render_features_summary_label = QLabel(
            self.render_features_summary_text()
        )
        self.render_features_summary_label.setObjectName("MicroLabel")
        self.render_features_summary_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.render_features_summary_label.setToolTip(
            "What's currently switched on/off for the next render -- "
            "Auto Cuts, Filters, and Emoji toggles all read back here."
        )

        video_stack = QWidget()
        video_stack.setObjectName("VideoStack")
        video_stack_layout = QVBoxLayout(video_stack)
        video_stack_layout.setContentsMargins(0, 0, 0, 0)
        video_stack_layout.setSpacing(6)
        video_stack_layout.addWidget(self.program_monitor_viewport, 1)
        video_stack_layout.addLayout(playback)
        video_stack_layout.addLayout(title_controls)

        self.timeline = SuggestionSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.emoji_feature_enabled = self.emoji_enabled
        self.timeline.sliderMoved.connect(self.seek_video)
        self.timeline.sliderReleased.connect(self.seek_to_slider_position)
        self.timeline.suggestionClicked.connect(self.select_ai_suggestion)
        self.timeline.selectionChanged.connect(self.timeline_selection_changed)
        self.timeline.viewportChanged.connect(self.timeline_viewport_changed)
        self.timeline.assetClipSelected.connect(self.editor_asset_clip_selected)
        self.timeline.assetClipChanged.connect(self.editor_asset_clip_changed)
        self.timeline.assetClipDoubleClicked.connect(
            self.editor_asset_clip_double_clicked
        )

        timeline_panel = QWidget()
        timeline_panel.setObjectName("TimelinePanel")
        self.timeline_panel = timeline_panel
        timeline_panel_layout = QVBoxLayout(timeline_panel)
        timeline_panel_layout.setContentsMargins(0, 0, 0, 0)
        timeline_panel_layout.setSpacing(8)
        self.timeline_panel_layout = timeline_panel_layout

        timeline_tools = QVBoxLayout()
        timeline_tools.setSpacing(4)
        timeline_header_row = QHBoxLayout()
        timeline_header_row.setSpacing(8)
        timeline_zoom_row = QHBoxLayout()
        timeline_zoom_row.setSpacing(8)

        timeline_title = QLabel("EDITOR TIMELINE")
        timeline_title.setObjectName("SectionTitle")

        self.timeline_time_label = QLabel("00:00.000 / 00:00.000")
        self.timeline_time_label.setObjectName("TimeLabel")
        self.timeline_time_label.setMinimumWidth(0)
        self.timeline_time_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        zoom_out_label = QLabel("-")
        zoom_out_label.setObjectName("MicroLabel")

        self.timeline_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_zoom_slider.setObjectName("TimelineZoom")
        self.timeline_zoom_slider.setRange(0, 100)
        self.timeline_zoom_slider.setValue(0)
        self.timeline_zoom_slider.setMinimumWidth(84)
        self.timeline_zoom_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.timeline_zoom_slider.valueChanged.connect(
            self.timeline_zoom_slider_changed
        )

        zoom_in_label = QLabel("+")
        zoom_in_label.setObjectName("MicroLabel")

        self.fit_selection_button = QPushButton("FIT")
        self.fit_selection_button.setObjectName("TinyButton")
        self.fit_selection_button.setToolTip("Fit the current IN/OUT selection on the timeline. Shortcut: F")
        self.fit_selection_button.clicked.connect(self.fit_timeline_selection)

        self.fit_source_button = QPushButton("SRC")
        self.fit_source_button.setObjectName("TinyButton")
        self.fit_source_button.setToolTip("Fit the entire source on the timeline. Shortcut: Ctrl+0")
        self.fit_source_button.clicked.connect(self.fit_timeline_source)

        timeline_header_row.addWidget(timeline_title)
        timeline_header_row.addStretch()
        timeline_header_row.addWidget(self.timeline_time_label)
        timeline_header_row.addWidget(self.fit_selection_button)
        timeline_header_row.addWidget(self.fit_source_button)
        timeline_zoom_row.addWidget(zoom_out_label)
        timeline_zoom_row.addWidget(self.timeline_zoom_slider, 1)
        timeline_zoom_row.addWidget(zoom_in_label)
        timeline_tools.addLayout(timeline_header_row)
        timeline_tools.addLayout(timeline_zoom_row)
        self.timeline_tools = timeline_tools

        self.timeline_navigator = TimelineNavigator()
        self.timeline_navigator.setObjectName("TimelineNavigator")
        self.timeline_navigator.viewportChangeRequested.connect(
            self.timeline_navigator_changed
        )

        timeline_panel_layout.addLayout(timeline_tools)
        timeline_panel_layout.addWidget(self.timeline)

        self.timeline_item_inspector = QFrame()
        self.timeline_item_inspector.setObjectName("TimelineItemInspector")
        self.timeline_item_inspector.setVisible(False)
        self.timeline_item_inspector.setMinimumHeight(108)
        timeline_item_inspector_layout = QVBoxLayout(self.timeline_item_inspector)
        timeline_item_inspector_layout.setContentsMargins(9, 7, 9, 7)
        timeline_item_inspector_layout.setSpacing(5)

        self.timeline_item_inspector_header = QLabel("SELECTED: NONE")
        self.timeline_item_inspector_header.setObjectName("TimelineInspectorHeader")
        self.timeline_item_inspector_summary = QLabel()
        self.timeline_item_inspector_summary.setObjectName("TimelineInspectorSummary")
        self.timeline_item_inspector_summary.setWordWrap(True)

        inspector_common = QGridLayout()
        inspector_common.setHorizontalSpacing(7)
        inspector_common.setVerticalSpacing(3)
        self.timeline_inspector_start = QDoubleSpinBox()
        self.timeline_inspector_start.setObjectName("TimelineInspectorTime")
        self.timeline_inspector_end = QDoubleSpinBox()
        self.timeline_inspector_end.setObjectName("TimelineInspectorTime")
        for control in (self.timeline_inspector_start, self.timeline_inspector_end):
            control.setRange(0.0, 86_400.0)
            control.setDecimals(3)
            control.setSingleStep(0.1)
            control.setSuffix(" s")
            control.setMinimumWidth(88)
            control.valueChanged.connect(self.timeline_inspector_timing_changed)
        self.timeline_inspector_duration = QLabel("Duration: --")
        self.timeline_inspector_duration.setObjectName("TimelineInspectorValue")
        self.timeline_inspector_enabled = QCheckBox("Enabled")
        self.timeline_inspector_enabled.toggled.connect(self.timeline_inspector_enabled_changed)
        self.timeline_inspector_locked = QCheckBox("Locked")
        self.timeline_inspector_locked.setEnabled(False)

        inspector_common.addWidget(QLabel("Start"), 0, 0)
        inspector_common.addWidget(self.timeline_inspector_start, 0, 1)
        inspector_common.addWidget(QLabel("End"), 0, 2)
        inspector_common.addWidget(self.timeline_inspector_end, 0, 3)
        inspector_common.addWidget(self.timeline_inspector_duration, 1, 0, 1, 2)
        inspector_common.addWidget(self.timeline_inspector_enabled, 1, 2)
        inspector_common.addWidget(self.timeline_inspector_locked, 1, 3)

        self.timeline_inspector_emoji_controls = QWidget()
        emoji_controls = QGridLayout(self.timeline_inspector_emoji_controls)
        emoji_controls.setContentsMargins(0, 0, 0, 0)
        emoji_controls.setHorizontalSpacing(7)
        emoji_controls.setVerticalSpacing(0)
        self.timeline_inspector_emoji_x = QDoubleSpinBox()
        self.timeline_inspector_emoji_y = QDoubleSpinBox()
        self.timeline_inspector_emoji_scale = QDoubleSpinBox()
        for control, maximum in (
            (self.timeline_inspector_emoji_x, 1.0),
            (self.timeline_inspector_emoji_y, 1.0),
            (self.timeline_inspector_emoji_scale, 3.0),
        ):
            control.setRange(0.0 if maximum == 1.0 else 0.25, maximum)
            control.setDecimals(2)
            control.setSingleStep(0.05)
            control.setMinimumWidth(72)
            control.valueChanged.connect(self.timeline_inspector_emoji_transform_changed)
        emoji_controls.addWidget(QLabel("X"), 0, 0)
        emoji_controls.addWidget(self.timeline_inspector_emoji_x, 0, 1)
        emoji_controls.addWidget(QLabel("Y"), 0, 2)
        emoji_controls.addWidget(self.timeline_inspector_emoji_y, 0, 3)
        emoji_controls.addWidget(QLabel("Scale"), 0, 4)
        emoji_controls.addWidget(self.timeline_inspector_emoji_scale, 0, 5)

        timeline_item_inspector_layout.addWidget(self.timeline_item_inspector_header)
        timeline_item_inspector_layout.addWidget(self.timeline_item_inspector_summary)
        timeline_item_inspector_layout.addLayout(inspector_common)
        timeline_item_inspector_layout.addWidget(self.timeline_inspector_emoji_controls)
        timeline_panel_layout.addWidget(self.timeline_item_inspector)

        self.suggestions_label = QLabel("AI clips appear as purple ranges on V1. Click a range to load that pick, then drag IN / OUT handles to tune the cut.")
        self.suggestions_label.setObjectName("SuggestionLabel")
        self.suggestions_label.setWordWrap(True)

        trim_help = QLabel("IN   ●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●   OUT")
        trim_help.setObjectName("TrimHelp")
        trim_help.setAlignment(Qt.AlignmentFlag.AlignCenter)

        selection_frame = QFrame()
        selection_frame.setObjectName("SubPanel")
        selection_layout = QVBoxLayout(selection_frame)
        selection_layout.setContentsMargins(8, 5, 8, 5)
        selection_layout.setSpacing(4)

        selection_controls = QHBoxLayout()
        selection_controls.setSpacing(8)

        self.start_button = QPushButton("Set Start")
        self.end_button = QPushButton("Set End")
        self.start_button.setObjectName("TinyButton")
        self.end_button.setObjectName("TinyButton")
        self.start_button.clicked.connect(self.set_start)
        self.end_button.clicked.connect(self.set_end)

        self.selection_label = QLabel("Selection: 00:00 → 00:00")
        self.selection_label.setObjectName("SelectionLabel")
        self.selection_label.setWordWrap(True)
        self.selection_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        self.render_features_summary_label.setWordWrap(True)
        self.render_features_summary_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.render_features_summary_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        selection_controls.addWidget(self.start_button)
        selection_controls.addWidget(self.end_button)
        selection_controls.addWidget(self.selection_label, 1)
        selection_layout.addLayout(selection_controls)
        selection_layout.addWidget(self.render_features_summary_label)

        preview_layout.addLayout(preview_header)
        preview_layout.addWidget(video_stack, 1)
        preview_layout.addWidget(self.suggestions_label)

        timeline_footer = QFrame()
        timeline_footer.setObjectName("TimelineFooter")
        self.timeline_footer = timeline_footer
        timeline_footer_layout = QVBoxLayout(timeline_footer)
        timeline_footer_layout.setContentsMargins(8, 6, 8, 6)
        timeline_footer_layout.setSpacing(6)

        # Keep navigator, legend, and selection feedback in distinct rows so
        # each remains readable as the editor column changes width.
        timeline_footer_layout.addWidget(self.timeline_navigator)

        timeline_footer_layout.addWidget(selection_frame)
        timeline_panel_layout.addWidget(timeline_footer)

        timeline_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.update_timeline_panel_minimum_height()

        center_editor_layout.addWidget(preview_frame, 1)

        audio_frame = QFrame()
        audio_frame.setObjectName("Panel")
        audio_layout = QVBoxLayout(audio_frame)
        audio_layout.setContentsMargins(16, 14, 16, 14)
        audio_layout.setSpacing(8)

        audio_header_row = QHBoxLayout()
        audio_header_row.setSpacing(8)
        audio_sfx_mode_row = QHBoxLayout()
        audio_sfx_mode_row.setSpacing(8)
        audio_sfx_actions_row = QVBoxLayout()
        audio_sfx_actions_row.setSpacing(8)
        audio_music_actions_row = QVBoxLayout()
        audio_music_actions_row.setSpacing(8)
        audio_music_volume_row = QHBoxLayout()
        audio_music_volume_row.setSpacing(8)

        audio_title = QLabel("AUDIO")
        audio_title.setObjectName("SectionTitle")


        sfx_mode_label = QLabel("Sound FX")
        sfx_mode_label.setObjectName("MicroLabel")

        self.sfx_mode_combo = QComboBox()
        self.sfx_mode_combo.setObjectName("CompactCombo")
        self.sfx_mode_combo.addItems(
            [
                "AUTO",
                "OFF",
            ]
        )
        self.sfx_mode_combo.setCurrentText(
            self.sfx_mode
        )
        self.sfx_mode_combo.setToolTip(
            "AUTO plans safe local or generated sound effects. OFF leaves SFX out of the render."
        )
        self.sfx_mode_combo.currentTextChanged.connect(
            self.sfx_mode_changed
        )

        self.generate_sfx_button = QPushButton("Generate SFX")
        self.generate_sfx_button.setObjectName("QuietButton")
        self.generate_sfx_button.setToolTip(
            "Plan editable sound-effect clips for the current selection."
        )
        self.generate_sfx_button.setEnabled(False)
        self.generate_sfx_button.clicked.connect(self.generate_sfx)

        self.open_sfx_folder_button = QPushButton("SFX Folder")
        self.open_sfx_folder_button.setObjectName("QuietButton")
        self.open_sfx_folder_button.setToolTip(
            "Open assets/sfx. Add audio files with descriptive names like whoosh, impact, pop, money, or glitch."
        )
        self.open_sfx_folder_button.clicked.connect(self.open_sfx_folder)

        self.music_button = QPushButton("♫ Add Music")
        self.music_button.setObjectName("MusicButton")
        self.music_button.setToolTip("Import an MP3, WAV, M4A, AAC, FLAC, or OGG file to mix under the Short.")
        self.music_button.clicked.connect(self.choose_music)

        self.music_label = QLabel("No background music")
        self.music_label.setObjectName("MusicLabel")
        self.music_label.setWordWrap(True)

        self.music_volume_label = QLabel("18%")
        self.music_volume_label.setObjectName("MusicVolumeLabel")

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setObjectName("MusicVolumeSlider")
        self.music_volume_slider.setRange(0, 50)
        self.music_volume_slider.setValue(self.music_volume)
        self.music_volume_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.music_volume_slider.valueChanged.connect(self.music_volume_changed)

        self.clear_music_button = QPushButton("Remove")
        self.clear_music_button.setObjectName("QuietButton")
        self.clear_music_button.setEnabled(False)
        self.clear_music_button.clicked.connect(self.clear_music)

        self.narrator_button = QPushButton("🎙 AI Narrator · Soon")
        self.narrator_button.setObjectName("QuietButton")
        self.narrator_button.setEnabled(False)
        self.narrator_button.setToolTip("Planned: generate and mix AI narration/commentary over selected source clips.")

        self.sfx_context_frame = QFrame()
        self.sfx_context_frame.setObjectName("SubPanel")
        self.sfx_context_frame.setVisible(False)

        sfx_context_layout = QHBoxLayout(self.sfx_context_frame)
        sfx_context_layout.setContentsMargins(8, 6, 8, 6)
        sfx_context_layout.setSpacing(8)

        sfx_selected_label = QLabel("Selected SFX")
        sfx_selected_label.setObjectName("MicroLabel")

        self.sfx_clip_label = QLabel("No SFX selected")
        self.sfx_clip_label.setObjectName("MusicLabel")
        self.sfx_clip_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.sfx_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sfx_volume_slider.setObjectName("MusicVolumeSlider")
        self.sfx_volume_slider.setRange(0, 80)
        self.sfx_volume_slider.setValue(25)
        self.sfx_volume_slider.setFixedWidth(96)
        self.sfx_volume_slider.setEnabled(False)
        self.sfx_volume_slider.setToolTip(
            "Selected SFX clip volume. This affects preview and final SFX mix."
        )
        self.sfx_volume_slider.valueChanged.connect(self.sfx_volume_changed)

        self.swap_sfx_button = QPushButton("Swap")
        self.swap_sfx_button.setObjectName("QuietButton")
        self.swap_sfx_button.setEnabled(False)
        self.swap_sfx_button.setToolTip(
            "Replace the selected SFX clip while keeping its timing."
        )
        self.swap_sfx_button.clicked.connect(self.swap_selected_sfx_clip)

        self.disable_sfx_button = QPushButton("Disable")
        self.disable_sfx_button.setObjectName("QuietButton")
        self.disable_sfx_button.setEnabled(False)
        self.disable_sfx_button.clicked.connect(self.toggle_selected_sfx_clip)

        self.delete_sfx_button = QPushButton("Delete")
        self.delete_sfx_button.setObjectName("CutButton")
        self.delete_sfx_button.setEnabled(False)
        self.delete_sfx_button.clicked.connect(self.delete_selected_sfx_clip)

        sfx_context_layout.addWidget(sfx_selected_label)
        sfx_context_layout.addWidget(self.sfx_clip_label, 1)
        sfx_context_layout.addWidget(self.sfx_volume_slider)
        sfx_context_layout.addWidget(self.swap_sfx_button)
        sfx_context_layout.addWidget(self.disable_sfx_button)
        sfx_context_layout.addWidget(self.delete_sfx_button)

        self.emoji_context_frame = QFrame()
        self.emoji_context_frame.setObjectName("SubPanel")
        self.emoji_context_frame.setVisible(False)

        emoji_context_layout = QHBoxLayout(self.emoji_context_frame)
        emoji_context_layout.setContentsMargins(8, 6, 8, 6)
        emoji_context_layout.setSpacing(8)

        emoji_selected_label = QLabel("Selected Emoji")
        emoji_selected_label.setObjectName("MicroLabel")

        self.emoji_clip_label = QLabel("No emoji selected")
        self.emoji_clip_label.setObjectName("MusicLabel")
        self.emoji_clip_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.swap_emoji_button = QPushButton("Swap")
        self.swap_emoji_button.setObjectName("QuietButton")
        self.swap_emoji_button.setEnabled(False)
        self.swap_emoji_button.setToolTip(
            "Replace the selected emoji reaction while keeping its timing."
        )
        self.swap_emoji_button.clicked.connect(self.swap_selected_emoji_clip)

        self.disable_emoji_button = QPushButton("Disable")
        self.disable_emoji_button.setObjectName("QuietButton")
        self.disable_emoji_button.setEnabled(False)
        self.disable_emoji_button.clicked.connect(self.toggle_selected_emoji_clip)

        self.delete_emoji_button = QPushButton("Delete")
        self.delete_emoji_button.setObjectName("CutButton")
        self.delete_emoji_button.setEnabled(False)
        self.delete_emoji_button.clicked.connect(self.delete_selected_emoji_clip)

        emoji_context_layout.addWidget(emoji_selected_label)
        emoji_context_layout.addWidget(self.emoji_clip_label, 1)
        emoji_context_layout.addWidget(self.swap_emoji_button)
        emoji_context_layout.addWidget(self.disable_emoji_button)
        emoji_context_layout.addWidget(self.delete_emoji_button)

        audio_header_row.addWidget(audio_title)
        audio_header_row.addStretch()
        audio_sfx_mode_row.addWidget(sfx_mode_label)
        audio_sfx_mode_row.addWidget(self.sfx_mode_combo, 1)
        audio_sfx_actions_row.addWidget(self.generate_sfx_button)
        audio_sfx_actions_row.addWidget(self.open_sfx_folder_button)
        audio_music_actions_row.addWidget(self.music_button)
        audio_music_actions_row.addWidget(self.clear_music_button)
        audio_music_volume_row.addWidget(QLabel("Music"))
        audio_music_volume_row.addWidget(self.music_volume_slider, 1)
        audio_music_volume_row.addWidget(self.music_volume_label)

        self.narrator_button.setMinimumWidth(0)
        self.narrator_button.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        audio_layout.addLayout(audio_header_row)
        audio_layout.addLayout(audio_sfx_mode_row)
        audio_layout.addLayout(audio_sfx_actions_row)
        audio_layout.addLayout(audio_music_actions_row)
        audio_layout.addWidget(self.music_label)
        audio_layout.addLayout(audio_music_volume_row)
        audio_layout.addWidget(self.narrator_button)
        audio_layout.addWidget(self.sfx_context_frame)

        log_frame = QFrame()
        log_frame.setObjectName("RenderLogPanel")
        self.render_log_frame = log_frame
        log_frame.setMinimumHeight(180)
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_header.setSpacing(10)

        log_title = QLabel("RENDER LOG")
        log_title.setObjectName("SectionTitle")

        log_header.addWidget(log_title)
        log_header.addStretch()

        self.render_log = QTextEdit()
        self.render_log.setObjectName("RenderLog")
        self.render_log.setReadOnly(True)
        self.render_log.setMinimumHeight(112)
        self.render_log.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.render_log.setPlaceholderText("Render progress will appear here...")

        log_layout.addLayout(log_header)
        log_layout.addWidget(self.render_log, 1)

        center_column.addWidget(center_editor_stack, 1)

        workspace.addWidget(center_scroll)

        # ----------------------------------------------------
        # RIGHT EDITOR COLUMN
        # ----------------------------------------------------

        right_column = QWidget()
        right_column.setObjectName("RightEditorContent")
        # Compact desktops can still keep the source rail and portrait
        # preview usable, while ordinary desktop widths give this editor
        # column the larger share of the workspace.
        # The scroll area's outer width is protected below. Its content is a
        # fixed-height vertical stack, so compact desktops scroll instead of
        # collapsing the timeline's lower lanes behind splitter handles.
        right_column.setMinimumWidth(0)
        right_column.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        right_column_layout = QVBoxLayout(right_column)
        right_column_layout.setContentsMargins(0, 0, 0, 0)
        right_column_layout.setSpacing(12)
        right_column_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.right_editor_content = right_column

        ai_frame = QFrame()
        ai_frame.setObjectName("Panel")
        self.ai_clip_hunter_frame = ai_frame
        ai_layout = QVBoxLayout(ai_frame)
        ai_layout.setContentsMargins(12, 14, 12, 14)
        ai_layout.setSpacing(10)

        ai_header = QVBoxLayout()
        ai_title = QLabel("AI CLIP HUNTER")
        ai_title.setObjectName("SectionTitle")

        ai_hint = QLabel("UP TO 6 PICKS")
        ai_hint.setObjectName("MicroBadge")

        ai_header.addWidget(ai_title)
        ai_header.addWidget(ai_hint)

        self.clip_cards_layout = QGridLayout()
        self.clip_cards_layout.setHorizontalSpacing(0)
        self.clip_cards_layout.setVerticalSpacing(10)
        self.clip_cards_layout.setColumnStretch(0, 1)

        self.clip_cards = []
        for index in range(6):
            card = QPushButton(f"AI PICK #{index + 1}\nRun Find Best Clips to populate")
            card.setObjectName("ClipCard")
            card.setProperty("selected", False)
            card.setMinimumHeight(92)
            card.setMinimumWidth(0)
            card.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Minimum,
            )
            card.setEnabled(False)
            card.setVisible(False)
            card.clicked.connect(lambda checked=False, card_index=index: (self.select_ai_card(card_index)))
            self.clip_cards.append(card)
            self.clip_cards_layout.addWidget(card, index, 0)

        ai_layout.addLayout(ai_header)
        ai_layout.addLayout(self.clip_cards_layout)

        visual_frame = QFrame()
        visual_frame.setObjectName("Panel")
        self.visual_effects_frame = visual_frame

        visual_layout = QVBoxLayout(visual_frame)
        visual_layout.setContentsMargins(12, 14, 12, 14)
        visual_layout.setSpacing(8)

        visual_header = QHBoxLayout()
        visual_title = QLabel("VISUALS & REACTIONS")
        visual_title.setObjectName("SectionTitle")
        visual_header.addWidget(visual_title)
        visual_header.addStretch()

        visual_hint = QLabel(
            "Smart motion and visual FX render with emoji reactions."
        )
        visual_hint.setObjectName("HintLabel")
        visual_hint.setWordWrap(True)

        emoji_actions = QHBoxLayout()
        emoji_actions.setSpacing(8)
        self.generate_emoji_button = QPushButton("GENERATE EMOJI")
        self.generate_emoji_button.setObjectName("QuietButton")
        self.generate_emoji_button.setToolTip(
            "Resolve emoji reactions for the current selection so their "
            "timing and placements are ready before rendering."
        )
        self.generate_emoji_button.setEnabled(False)
        self.generate_emoji_button.clicked.connect(self.generate_emoji)
        emoji_actions.addWidget(self.generate_emoji_button, 1)

        self.emoji_button = QPushButton(
            "EMOJI: ON" if self.emoji_enabled else "EMOJI: OFF"
        )
        self.emoji_button.setObjectName("EmojiToggle")
        self.emoji_button.setCheckable(True)
        self.emoji_button.setChecked(self.emoji_enabled)
        self.emoji_button.setToolTip(
            "Turns emoji reactions on or off for the next render."
        )
        self.emoji_button.clicked.connect(self.emoji_toggled)
        emoji_actions.addWidget(self.emoji_button)

        emoji_min_row = QHBoxLayout()
        emoji_min_row.setSpacing(8)
        emoji_min_label = QLabel("MIN EMOJI")
        emoji_min_label.setObjectName("TinyLabel")
        self.min_emoji_events_spinbox = QSpinBox()
        self.min_emoji_events_spinbox.setObjectName("CompactSpinBox")
        self.min_emoji_events_spinbox.setRange(0, 10)
        self.min_emoji_events_spinbox.setValue(self.min_emoji_events)
        self.min_emoji_events_spinbox.setToolTip(
            "Minimum number of emoji reactions for the current render."
        )
        self.min_emoji_events_spinbox.valueChanged.connect(
            self.min_emoji_events_changed
        )
        emoji_min_row.addWidget(emoji_min_label)
        emoji_min_row.addWidget(self.min_emoji_events_spinbox)
        emoji_min_row.addStretch()

        visual_layout.addLayout(visual_header)
        visual_layout.addWidget(visual_hint)
        visual_layout.addLayout(emoji_actions)
        visual_layout.addLayout(emoji_min_row)

        transcript_frame = QFrame()
        transcript_frame.setObjectName("Panel")
        self.transcript_frame = transcript_frame
        transcript_frame.setMinimumHeight(300)
        transcript_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        transcript_layout = QVBoxLayout(transcript_frame)
        transcript_layout.setContentsMargins(16, 16, 16, 16)
        transcript_layout.setSpacing(10)

        transcript_header = QHBoxLayout()
        transcript_title = QLabel("TRANSCRIPT SCRAP")
        transcript_title.setObjectName("SectionTitle")

        self.transcript_status_label = QLabel("Run Find Best Clips to load the source transcript.")
        self.transcript_status_label.setObjectName("TranscriptStatus")
        self.transcript_status_label.setWordWrap(True)

        transcript_header.addWidget(transcript_title)
        transcript_header.addSpacing(8)
        transcript_header.addWidget(self.transcript_status_label, 1)

        # Two rows, not one -- four buttons packed into a single row
        # overflow a narrow right column before they can shrink below
        # their own minimum width, silently clipping the last button(s).
        transcript_actions_top = QHBoxLayout()
        transcript_actions_top.setSpacing(8)
        transcript_actions_bottom = QHBoxLayout()
        transcript_actions_bottom.setSpacing(8)

        self.edit_transcript_button = QPushButton("✎ EDIT TEXT")
        self.edit_transcript_button.setObjectName("QuietButton")
        self.edit_transcript_button.setToolTip(
            "Correct the wording for this transcript line. The corrected text will be used for captions."
        )
        self.edit_transcript_button.clicked.connect(
            self.edit_selected_transcript_segment
        )

        self.reset_transcript_text_button = QPushButton("↶ RESET TEXT")
        self.reset_transcript_text_button.setObjectName("QuietButton")
        self.reset_transcript_text_button.setToolTip(
            "Restore Whisper's original wording for this transcript line."
        )
        self.reset_transcript_text_button.clicked.connect(
            self.reset_selected_transcript_text
        )

        self.cut_transcript_button = QPushButton("✕ CUT")
        self.cut_transcript_button.setObjectName("CutButton")
        self.cut_transcript_button.setToolTip(
            "Mark the selected transcript segment for removal from the final Short."
        )
        self.cut_transcript_button.clicked.connect(
            self.cut_selected_transcript_segment
        )

        self.restore_transcript_button = QPushButton("↺ UNCUT")
        self.restore_transcript_button.setObjectName("RestoreButton")
        self.restore_transcript_button.setToolTip(
            "Restore the selected transcript segment if it was marked for removal."
        )
        self.restore_transcript_button.clicked.connect(
            self.restore_selected_transcript_segment
        )

        transcript_actions_top.addWidget(self.edit_transcript_button, 1)
        transcript_actions_top.addWidget(self.reset_transcript_text_button, 1)
        transcript_actions_bottom.addWidget(self.cut_transcript_button, 1)
        transcript_actions_bottom.addWidget(self.restore_transcript_button, 1)

        for control in (
            self.edit_transcript_button,
            self.reset_transcript_text_button,
            self.cut_transcript_button,
            self.restore_transcript_button,
        ):
            control.setMinimumWidth(0)
            control.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )

        self.transcript_list = QListWidget()
        self.transcript_list.setObjectName("TranscriptList")
        self.transcript_list.setMinimumHeight(160)
        self.transcript_list.setAlternatingRowColors(False)
        self.transcript_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.transcript_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.transcript_list.itemClicked.connect(self.transcript_item_clicked)
        self.transcript_list.itemDoubleClicked.connect(
            self.edit_transcript_item
        )

        transcript_layout.addLayout(transcript_header)
        transcript_layout.addLayout(transcript_actions_top)
        transcript_layout.addLayout(transcript_actions_bottom)
        transcript_layout.addWidget(self.transcript_list, 1)

        # The workflow controls remain in the left rail. The right side owns
        # the editing and render-observation surfaces only.
        standard_short_mode_layout.takeAt(standard_short_mode_layout.count() - 1)
        standard_short_mode_layout.addWidget(ai_frame)
        standard_short_mode_layout.addWidget(visual_frame)
        standard_short_mode_layout.addWidget(audio_frame)
        standard_short_mode_layout.addStretch()

        log_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        right_column_layout.addWidget(timeline_panel)
        right_column_layout.addWidget(transcript_frame)
        right_column_layout.addWidget(log_frame)
        right_column.setMinimumHeight(
            timeline_panel.minimumHeight()
            + transcript_frame.minimumHeight()
            + log_frame.minimumHeight()
            + (right_column_layout.spacing() * 2)
        )

        right_scroll = QScrollArea()
        right_scroll.setObjectName("RightEditorScroll")
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setMinimumWidth(520)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        right_scroll.setWidget(right_column)
        self.right_editor_scroll = right_scroll
        self.update_timeline_panel_minimum_height()

        workspace.addWidget(right_scroll)
        workspace.setStretchFactor(0, 20)
        workspace.setStretchFactor(1, 37)
        workspace.setStretchFactor(2, 43)
        workspace.setSizes([340, 680, 760])
        self._default_main_splitter_sizes = [340, 680, 760]

        main_layout.addWidget(workspace, 1)

        # ----------------------------------------------------
        # PINNED GLOBAL ACTIVITY / PROGRESS STRIP
        # ----------------------------------------------------
        # This lives outside every scroll area and splitter, so it remains
        # visible at the bottom of the application at all times.
        self.global_progress_frame = QFrame()
        self.global_progress_frame.setObjectName(
            "GlobalProgressPanel"
        )
        self.global_progress_frame.setMinimumHeight(
            42
        )
        self.global_progress_frame.setMaximumHeight(
            48
        )

        global_progress_layout = QHBoxLayout(
            self.global_progress_frame
        )
        global_progress_layout.setContentsMargins(
            12,
            7,
            12,
            7,
        )
        global_progress_layout.setSpacing(
            10
        )

        self.render_progress_stage_label = QLabel(
            "READY"
        )
        self.render_progress_stage_label.setObjectName(
            "RenderProgressStage"
        )
        self.render_progress_stage_label.setMinimumWidth(
            150
        )

        self.render_progress_bar = QProgressBar()
        self.render_progress_bar.setObjectName(
            "RenderProgressBar"
        )
        self.render_progress_bar.setRange(
            0,
            100,
        )
        self.render_progress_bar.setValue(
            0
        )
        self.render_progress_bar.setTextVisible(
            False
        )
        self.render_progress_bar.setMinimumHeight(
            16
        )

        self.render_progress_time_label = QLabel(
            "Idle"
        )
        self.render_progress_time_label.setObjectName(
            "RenderProgressTime"
        )
        self.render_progress_time_label.setMinimumWidth(
            170
        )
        self.render_progress_time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        global_progress_layout.addWidget(
            self.render_progress_stage_label
        )
        global_progress_layout.addWidget(
            self.render_progress_bar,
            1,
        )
        global_progress_layout.addWidget(
            self.render_progress_time_label
        )

        main_layout.addWidget(
            self.global_progress_frame,
            0,
        )

    def update_timeline_panel_minimum_height(self):
        """Fit the timeline section to its visible controls and lane geometry."""

        required = (
            "timeline",
            "timeline_panel",
            "timeline_panel_layout",
            "timeline_tools",
            "timeline_footer",
            "timeline_item_inspector",
        )
        if not all(hasattr(self, name) for name in required):
            return

        inspector = self.timeline_item_inspector
        inspector_visible = inspector.isVisible()
        footer_height = self.timeline_footer.minimumSizeHint().height()
        minimum_height = (
            self.timeline.required_lane_stack_height()
            + self.timeline_tools.minimumSize().height()
            + footer_height
            + (self.timeline_panel_layout.spacing() * (3 if inspector_visible else 2))
        )
        if inspector_visible:
            minimum_height += inspector.minimumSizeHint().height()

        self.timeline_panel_minimum_height = minimum_height
        self.timeline_panel.setMinimumHeight(minimum_height)

        if all(
            hasattr(self, name)
            for name in ("right_editor_content", "transcript_frame", "render_log_frame")
        ):
            right_layout = self.right_editor_content.layout()
            self.right_editor_content.setMinimumHeight(
                minimum_height
                + self.transcript_frame.minimumHeight()
                + self.render_log_frame.minimumHeight()
                + (right_layout.spacing() * 2)
            )

    def keyPressEvent(
        self,
        event,
    ):

        if self.handle_editor_shortcut(
            event
        ):
            return

        super().keyPressEvent(
            event
        )

    def _point_in_widget(self, widget, event, watched=None) -> bool:
        try:
            point = event.position().toPoint()
            parent = widget.parentWidget()
            if watched is not None and parent is not None and watched is not parent:
                point = watched.mapTo(parent, point)
            return widget.geometry().contains(point)
        except AttributeError:
            return False

    def eventFilter(
        self,
        watched,
        event,
    ):

        video_widget = getattr(
            self,
            "video_widget",
            None,
        )
        preview_surface_widgets = (
            video_widget,
            getattr(self, "program_monitor_composition", video_widget),
        )

        title_handle_widgets = list(
            getattr(self, "persistent_title_resize_handles", {}).values()
        )
        title_label = getattr(self, "persistent_title_preview_label", None)

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and hasattr(self, "persistent_title_resize_handles")
        ):
            if self.begin_persistent_title_resize_drag(event, watched):
                event.accept()
                return True

        if (
            getattr(self, "persistent_title_resize_dragging", False)
            and event.type() == QEvent.Type.MouseMove
        ):
            self.update_persistent_title_resize_drag(event)
            event.accept()
            return True

        if (
            getattr(self, "persistent_title_resize_dragging", False)
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            self.finish_persistent_title_resize_drag()
            event.accept()
            return True

        if (
            event.type() == QEvent.Type.MouseButtonDblClick
            and event.button() == Qt.MouseButton.LeftButton
            and title_label is not None
            and title_label.isVisible()
            and (
                watched is title_label
                or (
                    watched in preview_surface_widgets
                    and self._point_in_widget(title_label, event, watched)
                )
            )
        ):
            self.edit_persistent_title_from_preview()
            event.accept()
            return True

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and (
                watched in preview_surface_widgets
                or watched is title_label
                or watched in title_handle_widgets
            )
        ):
            if self.begin_persistent_title_preview_drag(event, watched):
                event.accept()
                return True

        if (
            getattr(self, "persistent_title_dragging", False)
            and event.type() == QEvent.Type.MouseMove
        ):
            self.update_persistent_title_preview_drag(event)
            event.accept()
            return True

        if (
            getattr(self, "persistent_title_dragging", False)
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            self.finish_persistent_title_preview_drag()
            event.accept()
            return True

        emoji_handle_widgets = [
            handle
            for handles in getattr(self, "emoji_resize_handles", [])
            for handle in handles.values()
        ]

        if (
            event.type() == QEvent.Type.Enter
            and (
                watched in getattr(self, "emoji_preview_labels", [])
                or watched in emoji_handle_widgets
            )
        ):
            hover_index = None
            for index, label in enumerate(
                getattr(self, "emoji_preview_labels", [])
            ):
                if watched is label:
                    hover_index = index
                    break
            if hover_index is None:
                for index, handles in enumerate(
                    getattr(self, "emoji_resize_handles", [])
                ):
                    if watched in handles.values():
                        hover_index = index
                        break
            if hover_index is not None:
                self.set_emoji_resize_hover(hover_index)
        elif (
            event.type() == QEvent.Type.Leave
            and (
                watched in getattr(self, "emoji_preview_labels", [])
                or watched in emoji_handle_widgets
            )
            and not getattr(self, "emoji_resize_dragging", False)
        ):
            self.set_emoji_resize_hover(None)
        elif (
            event.type() == QEvent.Type.MouseMove
            and watched in preview_surface_widgets
            and not getattr(self, "emoji_preview_dragging", False)
            and not getattr(self, "emoji_resize_dragging", False)
        ):
            hover_slot = None
            try:
                point = self.preview_event_point(event, watched)
                for index, label in enumerate(
                    getattr(self, "emoji_preview_labels", [])
                ):
                    if label.isVisible() and label.geometry().adjusted(
                        -8, -8, 8, 8
                    ).contains(point):
                        hover_slot = index
                        break
            except Exception:
                hover_slot = None
            self.set_emoji_resize_hover(hover_slot)

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and hasattr(self, "emoji_resize_handles")
        ):
            if self.begin_emoji_resize_drag(event, watched):
                event.accept()
                return True

        if (
            getattr(self, "emoji_resize_dragging", False)
            and event.type() == QEvent.Type.MouseMove
        ):
            self.update_emoji_resize_drag(event)
            event.accept()
            return True

        if (
            getattr(self, "emoji_resize_dragging", False)
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            self.finish_emoji_resize_drag()
            event.accept()
            return True

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and (
                watched in preview_surface_widgets
                or watched in getattr(self, "emoji_preview_labels", [])
            )
        ):
            if self.begin_emoji_preview_drag(event, watched):
                event.accept()
                return True

        if (
            getattr(self, "emoji_preview_dragging", False)
            and event.type() == QEvent.Type.MouseMove
        ):
            self.update_emoji_preview_drag(event)
            event.accept()
            return True

        if (
            getattr(self, "emoji_preview_dragging", False)
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            self.finish_emoji_preview_drag()
            event.accept()
            return True

        if (
            event.type() == QEvent.Type.MouseButtonDblClick
            and event.button() == Qt.MouseButton.LeftButton
        ):
            for slot_index, label in enumerate(
                getattr(self, "emoji_preview_labels", [])
            ):
                if not label.isVisible():
                    continue
                hit = watched is label or (
                    watched in preview_surface_widgets
                    and self._point_in_widget(label, event, watched)
                )
                if hit:
                    self.open_emoji_picker(slot_index)
                    event.accept()
                    return True

        if (
            event.type() == QEvent.Type.MouseButtonDblClick
            and event.button() == Qt.MouseButton.LeftButton
        ):
            dbl_click_caption_label = getattr(self, "caption_preview_label", None)
            if (
                dbl_click_caption_label is not None
                and dbl_click_caption_label.isVisible()
                and (
                    watched is dbl_click_caption_label
                    or (
                        watched in preview_surface_widgets
                        and self._point_in_widget(dbl_click_caption_label, event, watched)
                    )
                )
            ):
                self.open_caption_corrector(self.player.position())
                event.accept()
                return True

        caption_handle_widgets = list(
            getattr(self, "caption_resize_handles", {}).values()
        )
        caption_label = getattr(self, "caption_preview_label", None)

        if (
            event.type() == QEvent.Type.Enter
            and (watched is caption_label or watched in caption_handle_widgets)
        ):
            self.set_caption_resize_hover(True)
        elif (
            event.type() == QEvent.Type.Leave
            and (watched is caption_label or watched in caption_handle_widgets)
            and not getattr(self, "caption_resize_dragging", False)
        ):
            self.set_caption_resize_hover(False)
        elif (
            event.type() == QEvent.Type.MouseMove
            and watched in preview_surface_widgets
            and caption_label is not None
            and caption_label.isVisible()
            and not getattr(self, "caption_preview_dragging", False)
            and not getattr(self, "caption_resize_dragging", False)
        ):
            try:
                hovering = caption_label.geometry().adjusted(
                    -8, -8, 8, 8
                ).contains(self.preview_event_point(event, watched))
            except Exception:
                hovering = False
            self.set_caption_resize_hover(hovering)

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and hasattr(self, "caption_resize_handles")
        ):
            if self.begin_caption_resize_drag(event, watched):
                event.accept()
                return True

        if (
            getattr(self, "caption_resize_dragging", False)
            and event.type() == QEvent.Type.MouseMove
        ):
            self.update_caption_resize_drag(event)
            event.accept()
            return True

        if (
            getattr(self, "caption_resize_dragging", False)
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            self.finish_caption_resize_drag()
            event.accept()
            return True

        if (
            not getattr(self, "emoji_preview_dragging", False)
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and (
                watched in preview_surface_widgets
                or watched is getattr(self, "caption_preview_label", None)
            )
        ):
            if self.begin_caption_preview_drag(event, watched):
                event.accept()
                return True

        if (
            getattr(self, "caption_preview_dragging", False)
            and event.type() == QEvent.Type.MouseMove
        ):
            self.update_caption_preview_drag(event)
            event.accept()
            return True

        if (
            getattr(self, "caption_preview_dragging", False)
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            self.finish_caption_preview_drag()
            event.accept()
            return True

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.RightButton
        ):
            for slot_index, label in enumerate(
                getattr(self, "emoji_preview_labels", [])
            ):
                if not label.isVisible():
                    continue
                if watched is label or (
                    watched in preview_surface_widgets
                    and self._point_in_widget(label, event, watched)
                ):
                    if self.reset_emoji_preview_position(slot_index):
                        event.accept()
                        return True
                    break

            caption_label = getattr(self, "caption_preview_label", None)
            if caption_label is not None and caption_label.isVisible():
                if watched is caption_label or (
                    watched in preview_surface_widgets
                    and self._point_in_widget(caption_label, event, watched)
                ):
                    self.reset_caption_position()
                    event.accept()
                    return True

        if (
            watched in preview_surface_widgets
            and event.type() == QEvent.Type.Resize
        ):
            QTimer.singleShot(
                0,
                lambda: self.update_emoji_preview_overlay(
                    self.player.position()
                ),
            )
            QTimer.singleShot(
                0,
                lambda: self.update_caption_preview_overlay(
                    self.player.position()
                ),
            )
            QTimer.singleShot(
                0,
                self.update_persistent_title_preview,
            )

        if (
            event.type()
            == QEvent.Type.KeyPress
            and self.handle_editor_shortcut(
                event,
                watched,
            )
        ):
            return True

        return super().eventFilter(
            watched,
            event,
        )

    def text_editor_has_focus(
        self,
        source_widget=None,
    ) -> bool:

        candidates = [
            source_widget,
            QApplication.focusWidget(),
        ]

        for widget in candidates:
            while widget is not None:
                if isinstance(
                    widget,
                    (
                        QLineEdit,
                        QPlainTextEdit,
                        QTextEdit,
                    ),
                ):
                    if hasattr(
                        widget,
                        "isReadOnly",
                    ) and widget.isReadOnly():
                        return False
                    return True

                if not hasattr(
                    widget,
                    "parentWidget",
                ):
                    break

                widget = widget.parentWidget()

        return False

    def handle_editor_shortcut(
        self,
        event,
        source_widget=None,
    ) -> bool:

        if self.text_editor_has_focus(
            source_widget
        ):
            return False

        key = event.key()
        modifiers = event.modifiers()

        if (
            QApplication.activeModalWidget() is None
            and key == Qt.Key.Key_Backspace
            and modifiers == Qt.KeyboardModifier.NoModifier
        ):
            if self.selected_sfx_clip() is not None:
                self.delete_selected_sfx_clip()
                event.accept()
                return True

        if (
            key == Qt.Key.Key_Space
            and modifiers == Qt.KeyboardModifier.NoModifier
        ):
            if not event.isAutoRepeat():
                self.toggle_playback()
            event.accept()
            return True

        if (
            key == Qt.Key.Key_F
            and modifiers == Qt.KeyboardModifier.NoModifier
        ):
            self.fit_timeline_selection()
            event.accept()
            return True

        if (
            key == Qt.Key.Key_0
            and modifiers
            & Qt.KeyboardModifier.ControlModifier
        ):
            self.fit_timeline_source()
            event.accept()
            return True

        return False

    def restore_layout_settings(self):

        for key, splitter_name in (
            ("main_splitter", "main_splitter"),
            ("preview_timeline_splitter", "preview_timeline_splitter"),
        ):
            splitter = getattr(
                self,
                splitter_name,
                None,
            )
            state = self.settings.value(
                f"layout/{key}"
            )
            if (
                key == "main_splitter"
                and self.settings.value("layout/main_splitter_schema")
                != "phase1d"
            ):
                continue
            if splitter is not None and state:
                splitter.restoreState(
                    state
                )

        # A previously-saved layout can leave the source/AI-hunter panels
        # narrower than what their own content needs to render legibly
        # (nothing in a splitter's saved state re-validates against the
        # panel's actual minimum content width). Fall back to the sane
        # hardcoded defaults if a restored size is below that floor.
        default_main_sizes = getattr(self, "_default_main_splitter_sizes", None)
        if self.main_splitter is not None and default_main_sizes:
            sizes = self.main_splitter.sizes()
            if len(sizes) == 3 and (sizes[0] < 320 or sizes[2] < 520):
                self.main_splitter.setSizes(default_main_sizes)

    def save_layout_settings(self):

        for key, splitter_name in (
            ("main_splitter", "main_splitter"),
            ("preview_timeline_splitter", "preview_timeline_splitter"),
        ):
            splitter = getattr(
                self,
                splitter_name,
                None,
            )
            if splitter is not None:
                self.settings.setValue(
                    f"layout/{key}",
                    splitter.saveState(),
                )
        self.settings.setValue("layout/main_splitter_schema", "phase1d")

    def closeEvent(
        self,
        event,
    ):

        self.save_layout_settings()
        super().closeEvent(
            event
        )



    def apply_style(self):

        self.setStyleSheet(
            STYLESHEET
        )


def main() -> int:

    app = QApplication(
        sys.argv
    )

    window = ShortsFactoryWindow()

    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
