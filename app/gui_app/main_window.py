from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QPoint, QProcess, QProcessEnvironment, QSettings, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
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
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from editor_asset_plan import load_editor_asset_plan
from visual_emphasis import DEFAULT_ENERGY, normalize_energy, normalize_sfx_mode

from .constants import ROOT
from .style import STYLESHEET
from .timeline_widget import SuggestionSlider
from .widgets import DropZone, TimelineNavigator

from .mixins.ai_clip_hunter import AIClipHunterMixin
from .mixins.ai_visual_pipeline import AIVisualPipelineMixin
from .mixins.ai_visual_preview import AIVisualPreviewMixin
from .mixins.ai_visual_slots import AIVisualSlotsMixin
from .mixins.editor_assets import EditorAssetsMixin
from .mixins.image_ai import ImageAIMixin
from .mixins.music import MusicMixin
from .mixins.playback import PlaybackMixin
from .mixins.render_pipeline import RenderPipelineMixin
from .mixins.settings import SettingsMixin
from .mixins.transcript import TranscriptMixin
from .mixins.web_images import WebImagesMixin


class ShortsFactoryWindow(
    QMainWindow,
    PlaybackMixin,
    TranscriptMixin,
    ImageAIMixin,
    SettingsMixin,
    AIVisualSlotsMixin,
    WebImagesMixin,
    AIVisualPipelineMixin,
    AIClipHunterMixin,
    MusicMixin,
    RenderPipelineMixin,
    EditorAssetsMixin,
    AIVisualPreviewMixin,
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

        self.visual_process = QProcess(self)

        self.visual_process.setWorkingDirectory(
            str(ROOT)
        )

        self.visual_process.setProcessEnvironment(
            process_env
        )

        self.visual_process.readyReadStandardOutput.connect(
            self.read_visual_output
        )

        self.visual_process.readyReadStandardError.connect(
            self.read_visual_error
        )

        self.visual_process.finished.connect(
            self.visual_plan_finished
        )

        self.visual_plan_slots: list[dict] = []
        self.visual_deleted_slots: list[dict] = []
        self.pending_visual_replan_mode = "replace"
        self.pending_visual_preserved_slots: list[dict] = []
        self.pending_visual_preserved_deleted_slots: list[dict] = []
        self.pending_visual_selected_slot_id: str | None = None
        self.editor_asset_plan: dict = load_editor_asset_plan()
        self.selected_sfx_clip_id: str | None = None
        self.sfx_preview_triggered: set[str] = set()
        self.active_visual_preview_clip_id: str | None = None
        self.active_visual_preview_signature: tuple | None = None
        self.active_visual_preview_layout_signature: tuple | None = None
        self.active_visual_preview_pixmap = QPixmap()
        self.visual_preview_dragging = False
        self.visual_preview_drag_origin = QPoint()
        self.visual_preview_drag_start_geometry = None
        self.visual_preview_drag_start_x = 0.0
        self.visual_preview_drag_start_y = 0.0

        self.visual_asset_process = QProcess(self)

        self.visual_asset_process.setWorkingDirectory(
            str(ROOT)
        )

        self.visual_asset_process.setProcessEnvironment(
            process_env
        )

        self.visual_asset_process.readyReadStandardOutput.connect(
            self.read_visual_asset_output
        )

        self.visual_asset_process.readyReadStandardError.connect(
            self.read_visual_asset_error
        )

        self.visual_asset_process.finished.connect(
            self.visual_asset_finished
        )

        # Openly licensed web-image search/download runs out-of-process so
        # network activity never freezes the editor. The selected image is
        # imported back into the same persistent visual entity.
        self.web_image_process = QProcess(self)
        self.web_image_process.setWorkingDirectory(
            str(ROOT)
        )
        self.web_image_process.setProcessEnvironment(
            process_env
        )
        self.web_image_process.readyReadStandardOutput.connect(
            self.read_web_image_output
        )
        self.web_image_process.readyReadStandardError.connect(
            self.read_web_image_error
        )
        self.web_image_process.finished.connect(
            self.web_image_finished
        )
        self.web_image_operation = ""
        self.web_image_target_slot_id = ""
        self.web_image_output_buffer = ""
        self.web_image_search_results: list[dict] = []
        self.web_image_results_path = (
            ROOT
            / "output"
            / "ai_visual_assets"
            / "web"
            / "search_results.json"
        )
        self.web_image_selection_path = (
            ROOT
            / "output"
            / "ai_visual_assets"
            / "web"
            / "selected_result.json"
        )

        self.image_status_process = QProcess(self)

        self.image_status_process.setWorkingDirectory(
            str(ROOT)
        )

        self.image_status_process.setProcessEnvironment(
            process_env
        )

        self.image_status_process.readyReadStandardOutput.connect(
            self.read_image_status_output
        )

        self.image_status_process.readyReadStandardError.connect(
            self.read_image_status_error
        )

        self.image_status_process.finished.connect(
            self.image_status_finished
        )

        self.image_status_stdout = ""
        self.image_status_stderr = ""
        self.pending_image_model_change = ""

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

        self.image_ai_state = "not_checked"
        self.image_ai_models: list[dict] = []
        self.current_image_model_title = ""
        self.selected_image_model_title = ""
        self.updating_image_model_combo = False
        self.image_quality = "BALANCED"
        self.visual_asset_output_buffer = ""
        self.visual_asset_provider = "auto"
        self.selected_visual_slot_index: int | None = None
        self.updating_visual_inspector = False
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
                "preview/volume",
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
                "transcription/quality",
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
                "render/edit_energy",
                DEFAULT_ENERGY,
            )
            or DEFAULT_ENERGY
        )
        self.sfx_mode = normalize_sfx_mode(
            self.settings.value(
                "render/sfx_mode",
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
        self.update_image_ai_indicator()
        self.load_selected_visual_into_inspector()
        self.load_editor_asset_plan_state()
        self.global_progress_timer.start()
        self.update_global_progress()
        # Do not probe or auto-launch Forge when ShortsFactory starts.
        # Image AI remains available only when the user explicitly invokes
        # an Image AI action later.

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

        main_layout.addWidget(header_frame)

        # ====================================================
        # WORKSPACE
        # ====================================================

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setObjectName("MainSplitter")
        workspace.setChildrenCollapsible(False)
        self.main_splitter = workspace

        # ----------------------------------------------------
        # LEFT RAIL / SOURCE PANEL
        # ----------------------------------------------------

        source_frame = QFrame()
        source_frame.setObjectName("Panel")
        source_frame.setMinimumWidth(220)
        source_frame.setMaximumWidth(440)

        source_layout = QVBoxLayout(source_frame)
        source_layout.setContentsMargins(16, 16, 16, 16)
        source_layout.setSpacing(12)

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

        edit_style_buttons = QHBoxLayout()
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
            edit_style_buttons.addWidget(button, 1)

        edit_style_layout.addLayout(edit_style_buttons)

        self.generate_button = QPushButton("Generate Short")
        self.generate_button.setObjectName("GenerateButton")
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self.generate_short)

        source_layout.addWidget(left_title)
        source_layout.addWidget(self.drop_zone, 1)
        source_layout.addWidget(self.file_label)
        source_layout.addWidget(source_hint)
        source_layout.addLayout(transcription_row)
        source_layout.addSpacing(6)
        source_layout.addWidget(self.find_clips_button)
        source_layout.addWidget(edit_style_frame)
        source_layout.addWidget(self.generate_button)

        workspace.addWidget(source_frame)

        # ----------------------------------------------------
        # CENTER / PREVIEW PANEL
        # ----------------------------------------------------

        center_widget = QWidget()
        center_widget.setObjectName("CenterColumn")
        center_widget.setMinimumWidth(520)

        center_scroll = QScrollArea()
        center_scroll.setObjectName("CenterScroll")
        center_scroll.setWidgetResizable(True)
        center_scroll.setFrameShape(QFrame.Shape.NoFrame)
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
        preview_layout.setContentsMargins(16, 16, 16, 14)
        preview_layout.setSpacing(10)

        preview_header = QHBoxLayout()
        preview_header.setSpacing(10)

        preview_title = QLabel("PREVIEW MONITOR")
        preview_title.setObjectName("SectionTitle")

        preview_tag = QLabel("TRIM / SEEK / AUDITION")
        preview_tag.setObjectName("MicroBadge")

        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        preview_header.addWidget(preview_tag)

        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("VideoPreview")
        self.video_widget.setMinimumSize(520, 260)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.player.setVideoOutput(self.video_widget)

        # Live AI visual preview. These labels sit above the source preview
        # and mirror the active AI_VISUAL clip at the current source time.
        self.ai_visual_preview_dim = QLabel(self.video_widget)
        self.ai_visual_preview_dim.setObjectName("VisualPreviewDim")
        self.ai_visual_preview_dim.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.ai_visual_preview_dim.hide()

        self.ai_visual_preview_overlay = QLabel(self.video_widget)
        self.ai_visual_preview_overlay.setObjectName("VisualPreviewOverlay")
        self.ai_visual_preview_overlay.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.ai_visual_preview_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            False,
        )
        self.ai_visual_preview_overlay.setMouseTracking(True)
        self.ai_visual_preview_overlay.setCursor(
            Qt.CursorShape.OpenHandCursor
        )
        self.ai_visual_preview_overlay.setToolTip(
            "Drag the active image to reposition it in the Short."
        )
        self.ai_visual_preview_overlay.hide()

        playback = QHBoxLayout()
        playback.setSpacing(10)

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
        self.preview_volume_slider.setFixedWidth(120)
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

        video_stack = QWidget()
        video_stack.setObjectName("VideoStack")
        video_stack_layout = QVBoxLayout(video_stack)
        video_stack_layout.setContentsMargins(0, 0, 0, 0)
        video_stack_layout.setSpacing(10)
        video_stack_layout.addWidget(self.video_widget, 1)
        video_stack_layout.addLayout(playback)

        self.timeline = SuggestionSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
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
        # Reserve enough parent geometry for the timeline widget, its toolbar,
        # and the full-source navigator. If the panel itself is shorter than
        # those children, Qt clips the bottom of the timeline even though the
        # timeline still reports its larger height; the SFX lane then exists
        # off-screen. The center column is scrollable, so protect the editor
        # lanes here instead of clipping them.
        timeline_panel.setMinimumHeight(350)
        timeline_panel_layout = QVBoxLayout(timeline_panel)
        timeline_panel_layout.setContentsMargins(0, 0, 0, 0)
        timeline_panel_layout.setSpacing(8)

        timeline_tools = QHBoxLayout()
        timeline_tools.setSpacing(8)

        timeline_title = QLabel("EDITOR TIMELINE")
        timeline_title.setObjectName("SectionTitle")

        self.timeline_time_label = QLabel("00:00.000 / 00:00.000")
        self.timeline_time_label.setObjectName("TimeLabel")

        zoom_out_label = QLabel("-")
        zoom_out_label.setObjectName("MicroLabel")

        self.timeline_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_zoom_slider.setObjectName("TimelineZoom")
        self.timeline_zoom_slider.setRange(0, 100)
        self.timeline_zoom_slider.setValue(0)
        self.timeline_zoom_slider.setFixedWidth(140)
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

        timeline_tools.addWidget(timeline_title)
        timeline_tools.addSpacing(8)
        timeline_tools.addWidget(self.timeline_time_label)
        timeline_tools.addStretch()
        timeline_tools.addWidget(zoom_out_label)
        timeline_tools.addWidget(self.timeline_zoom_slider)
        timeline_tools.addWidget(zoom_in_label)
        timeline_tools.addWidget(self.fit_selection_button)
        timeline_tools.addWidget(self.fit_source_button)

        self.timeline_navigator = TimelineNavigator()
        self.timeline_navigator.setObjectName("TimelineNavigator")
        self.timeline_navigator.viewportChangeRequested.connect(
            self.timeline_navigator_changed
        )

        timeline_panel_layout.addLayout(timeline_tools)
        timeline_panel_layout.addWidget(self.timeline, 1)
        timeline_panel_layout.addWidget(self.timeline_navigator)

        self.suggestions_label = QLabel("AI clips appear as purple ranges on V1. Click a range to load that pick, then drag IN / OUT handles to tune the cut.")
        self.suggestions_label.setObjectName("SuggestionLabel")
        self.suggestions_label.setWordWrap(True)

        trim_help = QLabel("IN   ●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●   OUT")
        trim_help.setObjectName("TrimHelp")
        trim_help.setAlignment(Qt.AlignmentFlag.AlignCenter)

        selection_frame = QFrame()
        selection_frame.setObjectName("SubPanel")
        selection_layout = QHBoxLayout(selection_frame)
        selection_layout.setContentsMargins(12, 10, 12, 10)
        selection_layout.setSpacing(10)

        self.start_button = QPushButton("Set Start")
        self.end_button = QPushButton("Set End")
        self.start_button.clicked.connect(self.set_start)
        self.end_button.clicked.connect(self.set_end)

        self.selection_label = QLabel("Selection: 00:00 → 00:00")
        self.selection_label.setObjectName("SelectionLabel")

        selection_layout.addWidget(self.start_button)
        selection_layout.addWidget(self.end_button)
        selection_layout.addSpacing(8)
        selection_layout.addWidget(self.selection_label, 1)

        self.preview_timeline_splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_timeline_splitter.setObjectName("PreviewTimelineSplitter")
        self.preview_timeline_splitter.setChildrenCollapsible(False)
        self.preview_timeline_splitter.addWidget(video_stack)
        self.preview_timeline_splitter.addWidget(timeline_panel)
        self.preview_timeline_splitter.setStretchFactor(0, 1)
        self.preview_timeline_splitter.setStretchFactor(1, 0)
        # Give the protected timeline panel real space inside the splitter.
        # Extra height scrolls in CenterScroll instead of cutting off SFX.
        self.preview_timeline_splitter.setMinimumHeight(690)
        self.preview_timeline_splitter.setSizes([330, 360])

        preview_layout.addLayout(preview_header)
        preview_layout.addWidget(self.preview_timeline_splitter, 1)
        preview_layout.addWidget(self.suggestions_label)
        preview_layout.addWidget(selection_frame)

        self.timeline_legend = QLabel(
            "TIMELINE  //  CYAN SOURCE   RED CUT   AMBER EDITED   GOLD CAPTION   VIOLET MOTION   MAGENTA FX   GREEN VISUAL/GRAPHIC"
        )
        self.timeline_legend.setObjectName("MicroLabel")
        self.timeline_legend.setToolTip(
            "Red = transcript cut, amber = corrected transcript text, "
            "bright cyan = real camera cut, violet = automatic motion, "
            "magenta = filter/FX hit, green = planned graphic or AI visual."
        )

        preview_layout.addWidget(
            self.timeline_legend
        )

        center_editor_layout.addWidget(preview_frame, 1)

        audio_frame = QFrame()
        audio_frame.setObjectName("Panel")
        audio_layout = QVBoxLayout(audio_frame)
        audio_layout.setContentsMargins(16, 14, 16, 14)
        audio_layout.setSpacing(8)

        audio_top_row = QHBoxLayout()
        audio_top_row.setSpacing(10)

        audio_bottom_row = QHBoxLayout()
        audio_bottom_row.setSpacing(10)

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

        self.music_volume_label = QLabel("18%")
        self.music_volume_label.setObjectName("MusicVolumeLabel")

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setObjectName("MusicVolumeSlider")
        self.music_volume_slider.setRange(0, 50)
        self.music_volume_slider.setValue(self.music_volume)
        self.music_volume_slider.setFixedWidth(130)
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

        audio_top_row.addWidget(audio_title)
        audio_top_row.addSpacing(12)
        audio_top_row.addWidget(sfx_mode_label)
        audio_top_row.addWidget(self.sfx_mode_combo)
        audio_top_row.addWidget(self.generate_sfx_button)
        audio_top_row.addWidget(self.open_sfx_folder_button)
        audio_top_row.addStretch()

        audio_bottom_row.addWidget(self.music_button)
        audio_bottom_row.addWidget(self.music_label, 1)
        audio_bottom_row.addWidget(QLabel("Music"))
        audio_bottom_row.addWidget(self.music_volume_slider)
        audio_bottom_row.addWidget(self.music_volume_label)
        audio_bottom_row.addWidget(self.clear_music_button)
        audio_bottom_row.addSpacing(10)
        audio_bottom_row.addWidget(self.narrator_button)

        audio_layout.addLayout(audio_top_row)
        audio_layout.addLayout(audio_bottom_row)
        audio_layout.addWidget(self.sfx_context_frame)

        center_editor_layout.addWidget(audio_frame, 0)

        log_frame = QFrame()
        log_frame.setObjectName("Panel")
        log_frame.setMinimumHeight(190)
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_header.setSpacing(10)

        log_title = QLabel("RENDER STATUS")
        log_title.setObjectName("SectionTitle")

        log_header.addWidget(log_title)
        log_header.addStretch()

        self.render_log = QTextEdit()
        self.render_log.setReadOnly(True)
        self.render_log.setMinimumHeight(112)
        self.render_log.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.render_log.setPlaceholderText("Render progress will appear here...")

        log_layout.addLayout(log_header)
        log_layout.addWidget(self.render_log, 1)

        center_column.addWidget(
            center_editor_stack,
            1,
        )
        center_column.addWidget(log_frame, 0)

        workspace.addWidget(center_scroll)

        # ----------------------------------------------------
        # RIGHT RAIL / AI + TRANSCRIPT
        # ----------------------------------------------------

        right_column = QSplitter(Qt.Orientation.Vertical)
        right_column.setObjectName("RightSplitter")
        right_column.setChildrenCollapsible(False)
        right_column.setMinimumWidth(320)
        self.right_splitter = right_column

        ai_frame = QFrame()
        ai_frame.setObjectName("Panel")
        ai_layout = QVBoxLayout(ai_frame)
        ai_layout.setContentsMargins(16, 16, 16, 16)
        ai_layout.setSpacing(10)

        ai_header = QHBoxLayout()
        ai_title = QLabel("AI CLIP HUNTER")
        ai_title.setObjectName("SectionTitle")

        ai_hint = QLabel("UP TO 6 PICKS")
        ai_hint.setObjectName("MicroBadge")

        ai_header.addWidget(ai_title)
        ai_header.addStretch()
        ai_header.addWidget(ai_hint)

        self.clip_cards_layout = QGridLayout()
        self.clip_cards_layout.setHorizontalSpacing(10)
        self.clip_cards_layout.setVerticalSpacing(10)

        self.clip_cards = []
        for index in range(6):
            card = QPushButton(f"AI PICK #{index + 1}\nRun Find Best Clips to populate")
            card.setObjectName("ClipCard")
            card.setProperty("selected", False)
            card.setMinimumHeight(92)
            card.setEnabled(False)
            card.setVisible(False)
            card.clicked.connect(lambda checked=False, card_index=index: (self.select_ai_card(card_index)))
            self.clip_cards.append(card)
            row = index // 2
            column = index % 2
            self.clip_cards_layout.addWidget(card, row, column)

        ai_layout.addLayout(ai_header)
        ai_layout.addLayout(self.clip_cards_layout)

        visual_frame = QFrame()
        visual_frame.setObjectName("Panel")

        visual_layout = QVBoxLayout(
            visual_frame
        )

        visual_layout.setContentsMargins(
            16,
            14,
            16,
            14,
        )

        visual_layout.setSpacing(
            8
        )

        visual_header = QHBoxLayout()

        visual_title = QLabel(
            "AI VISUAL CUTAWAYS"
        )
        visual_title.setObjectName(
            "SectionTitle"
        )

        self.plan_visuals_button = QPushButton(
            "✦ PLAN VISUALS"
        )
        self.plan_visuals_button.setObjectName(
            "QuietButton"
        )
        self.plan_visuals_button.setToolTip(
            "Add 0-2 new AI visual entities for the current selected clip. "
            "Existing image entities stay until you delete them."
        )
        self.plan_visuals_button.setEnabled(
            False
        )
        self.plan_visuals_button.clicked.connect(
            self.plan_ai_visuals
        )

        visual_header.addWidget(
            visual_title
        )
        visual_header.addStretch()
        visual_header.addWidget(
            self.plan_visuals_button
        )

        self.generate_visual_assets_button = QPushButton(
            "⬡ GENERATE ASSETS"
        )
        self.generate_visual_assets_button.setObjectName(
            "QuietButton"
        )
        self.generate_visual_assets_button.setToolTip(
            "Generate local image assets for the planned green cutaway slots. "
            "If no compatible image API is running, preview placeholders are created "
            "so the compositing pipeline can still be tested."
        )
        self.generate_visual_assets_button.setEnabled(
            False
        )
        self.generate_visual_assets_button.clicked.connect(
            self.generate_visual_assets
        )

        visual_header.addWidget(
            self.generate_visual_assets_button
        )

        self.check_image_ai_button = QPushButton(
            "CHECK IMAGE AI"
        )
        self.check_image_ai_button.setObjectName(
            "QuietButton"
        )
        self.check_image_ai_button.setToolTip(
            "Refresh the local image generator connection state."
        )
        self.check_image_ai_button.clicked.connect(
            self.check_image_ai
        )

        self.image_ai_status_label = QLabel(
            "● IMAGE AI NOT CHECKED"
        )
        self.image_ai_status_label.setObjectName(
            "ImageAIStatus"
        )
        self.image_ai_status_label.setProperty(
            "state",
            "not_checked",
        )

        image_status_row = QHBoxLayout()
        image_status_row.setSpacing(
            8
        )
        image_status_row.addWidget(
            self.image_ai_status_label,
            1,
        )
        image_status_row.addWidget(
            self.check_image_ai_button
        )

        self.image_model_combo = QComboBox()
        self.image_model_combo.setObjectName(
            "CompactCombo"
        )
        self.image_model_combo.setEnabled(
            False
        )
        self.image_model_combo.addItem(
            "No image model",
            "",
        )
        self.image_model_combo.currentIndexChanged.connect(
            self.image_model_changed
        )

        self.quality_combo = QComboBox()
        self.quality_combo.setObjectName(
            "CompactCombo"
        )
        self.quality_combo.addItems(
            [
                "FAST",
                "BALANCED",
                "HIGH",
            ]
        )
        self.quality_combo.setCurrentText(
            "BALANCED"
        )
        self.quality_combo.currentTextChanged.connect(
            self.quality_changed
        )

        model_row = QGridLayout()
        model_row.setHorizontalSpacing(
            8
        )
        model_row.setVerticalSpacing(
            6
        )

        image_model_label = QLabel(
            "IMAGE MODEL"
        )
        image_model_label.setObjectName(
            "MicroLabel"
        )

        quality_label = QLabel(
            "QUALITY"
        )
        quality_label.setObjectName(
            "MicroLabel"
        )

        model_row.addWidget(
            image_model_label,
            0,
            0,
        )
        model_row.addWidget(
            self.image_model_combo,
            0,
            1,
        )
        model_row.addWidget(
            quality_label,
            1,
            0,
        )
        model_row.addWidget(
            self.quality_combo,
            1,
            1,
        )

        self.visual_status_label = QLabel(
            "Load a transcript, then plan visuals for the active selection."
        )
        self.visual_status_label.setObjectName(
            "TranscriptStatus"
        )

        self.visual_slots_list = QListWidget()
        self.visual_slots_list.setObjectName(
            "TranscriptList"
        )
        self.visual_slots_list.setMinimumHeight(
            190
        )
        self.visual_slots_list.itemClicked.connect(
            self.visual_slot_clicked
        )

        self.visual_inspector = QFrame()
        self.visual_inspector.setObjectName(
            "SubPanel"
        )
        inspector_layout = QVBoxLayout(
            self.visual_inspector
        )
        inspector_layout.setContentsMargins(
            10,
            10,
            10,
            10,
        )
        inspector_layout.setSpacing(
            7
        )

        self.visual_inspector_title = QLabel(
            "SELECT IMAGE ENTITY"
        )
        self.visual_inspector_title.setObjectName(
            "SectionTitle"
        )

        inspector_grid = QGridLayout()
        inspector_grid.setHorizontalSpacing(
            8
        )
        inspector_grid.setVerticalSpacing(
            6
        )

        self.visual_label_edit = QLineEdit()
        self.visual_label_edit.setObjectName(
            "CompactLineEdit"
        )
        self.visual_start_edit = QLineEdit()
        self.visual_start_edit.setObjectName(
            "CompactLineEdit"
        )
        self.visual_end_edit = QLineEdit()
        self.visual_end_edit.setObjectName(
            "CompactLineEdit"
        )
        self.visual_type_edit = QLineEdit()
        self.visual_type_edit.setObjectName(
            "CompactLineEdit"
        )

        # Image acquisition is separate from the visual entity itself.
        # Every entity keeps its timing/transform/asset while this preference
        # decides which backend should supply its next image.
        self.visual_image_source_combo = QComboBox()
        self.visual_image_source_combo.setObjectName(
            "CompactCombo"
        )
        self.visual_image_source_combo.addItem(
            "FORGE · LOCAL AI",
            "FORGE",
        )
        self.visual_image_source_combo.addItem(
            "WEB IMAGE SEARCH",
            "WEB",
        )
        self.visual_image_source_combo.addItem(
            "CHATGPT IMAGE",
            "CHATGPT",
        )
        self.visual_image_source_combo.setToolTip(
            "Choose where the selected visual entity should get its next image."
        )

        self.visual_display_mode_combo = QComboBox()
        self.visual_display_mode_combo.setObjectName(
            "CompactCombo"
        )
        self.visual_display_mode_combo.addItems(
            [
                "OVERLAY_CARD",
                "FULL_FRAME_CONTAIN",
                "FULL_FRAME_COVER",
            ]
        )

        self.visual_scale_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.visual_scale_slider.setObjectName(
            "MusicVolumeSlider"
        )
        self.visual_scale_slider.setRange(
            60,
            140,
        )
        self.visual_scale_slider.setValue(
            100
        )

        self.visual_scale_label = QLabel(
            "100%"
        )
        self.visual_scale_label.setObjectName(
            "MusicVolumeLabel"
        )

        self.visual_x_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.visual_x_slider.setObjectName(
            "MusicVolumeSlider"
        )
        self.visual_x_slider.setRange(
            -100,
            100,
        )
        self.visual_x_slider.setValue(
            0
        )
        self.visual_x_label = QLabel(
            "0"
        )
        self.visual_x_label.setObjectName(
            "MusicVolumeLabel"
        )

        self.visual_y_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.visual_y_slider.setObjectName(
            "MusicVolumeSlider"
        )
        self.visual_y_slider.setRange(
            -100,
            100,
        )
        self.visual_y_slider.setValue(
            0
        )
        self.visual_y_label = QLabel(
            "0"
        )
        self.visual_y_label.setObjectName(
            "MusicVolumeLabel"
        )

        self.visual_label_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_start_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_end_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_type_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_image_source_combo.currentIndexChanged.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_display_mode_combo.currentTextChanged.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_scale_slider.valueChanged.connect(
            self.visual_scale_changed
        )
        self.visual_x_slider.valueChanged.connect(
            self.visual_position_slider_changed
        )
        self.visual_y_slider.valueChanged.connect(
            self.visual_position_slider_changed
        )

        inspector_grid.addWidget(
            QLabel("Label"),
            0,
            0,
        )
        inspector_grid.addWidget(
            self.visual_label_edit,
            0,
            1,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Start"),
            1,
            0,
        )
        inspector_grid.addWidget(
            self.visual_start_edit,
            1,
            1,
        )
        inspector_grid.addWidget(
            QLabel("End"),
            1,
            2,
        )
        inspector_grid.addWidget(
            self.visual_end_edit,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Type"),
            2,
            0,
        )
        inspector_grid.addWidget(
            self.visual_type_edit,
            2,
            1,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Source"),
            3,
            0,
        )
        inspector_grid.addWidget(
            self.visual_image_source_combo,
            3,
            1,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Mode"),
            4,
            0,
        )
        inspector_grid.addWidget(
            self.visual_display_mode_combo,
            4,
            1,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Scale"),
            5,
            0,
        )
        inspector_grid.addWidget(
            self.visual_scale_slider,
            5,
            1,
            1,
            2,
        )
        inspector_grid.addWidget(
            self.visual_scale_label,
            5,
            3,
        )
        inspector_grid.addWidget(
            QLabel("X"),
            6,
            0,
        )
        inspector_grid.addWidget(
            self.visual_x_slider,
            6,
            1,
            1,
            2,
        )
        inspector_grid.addWidget(
            self.visual_x_label,
            6,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Y"),
            7,
            0,
        )
        inspector_grid.addWidget(
            self.visual_y_slider,
            7,
            1,
            1,
            2,
        )
        inspector_grid.addWidget(
            self.visual_y_label,
            7,
            3,
        )

        self.visual_reason_label = QLabel(
            "Select an image thumbnail or green timeline block to edit it."
        )
        self.visual_reason_label.setObjectName(
            "HintLabel"
        )
        self.visual_reason_label.setWordWrap(
            True
        )

        self.visual_prompt_edit = QTextEdit()
        self.visual_prompt_edit.setObjectName(
            "PromptEdit"
        )
        self.visual_prompt_edit.setMaximumHeight(
            86
        )
        self.visual_prompt_edit.textChanged.connect(
            self.visual_prompt_changed
        )

        preview_row = QHBoxLayout()
        preview_row.setSpacing(
            10
        )

        self.visual_preview_label = QLabel(
            "NO IMAGE"
        )
        self.visual_preview_label.setObjectName(
            "VisualPreviewThumb"
        )
        self.visual_preview_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.visual_preview_label.setFixedSize(
            86,
            122,
        )

        action_column = QVBoxLayout()
        action_column.setSpacing(
            7
        )

        self.regenerate_visual_button = QPushButton(
            "REGENERATE"
        )
        self.regenerate_visual_button.setObjectName(
            "QuietButton"
        )
        self.regenerate_visual_button.clicked.connect(
            self.regenerate_selected_visual_asset
        )

        self.generate_more_visual_button = QPushButton(
            "GENERATE NEW IMAGE"
        )
        self.generate_more_visual_button.setObjectName(
            "QuietButton"
        )
        self.generate_more_visual_button.setToolTip(
            "Create another independent image entity with its own timeline clip and properties."
        )
        self.generate_more_visual_button.clicked.connect(
            self.generate_more_selected_visual_variant
        )

        self.disable_visual_button = QPushButton(
            "DISABLE"
        )
        self.disable_visual_button.setObjectName(
            "QuietButton"
        )
        self.disable_visual_button.clicked.connect(
            self.toggle_selected_visual_enabled
        )

        self.delete_visual_button = QPushButton(
            "DELETE"
        )
        self.delete_visual_button.setObjectName(
            "CutButton"
        )
        self.delete_visual_button.clicked.connect(
            self.delete_selected_visual
        )

        action_column.addWidget(
            self.regenerate_visual_button
        )
        action_column.addWidget(
            self.generate_more_visual_button
        )
        action_column.addWidget(
            self.disable_visual_button
        )
        action_column.addWidget(
            self.delete_visual_button
        )
        action_column.addStretch()

        preview_row.addWidget(
            self.visual_preview_label
        )
        preview_row.addLayout(
            action_column,
            1,
        )

        inspector_layout.addWidget(
            self.visual_inspector_title
        )
        inspector_layout.addLayout(
            inspector_grid
        )
        inspector_layout.addWidget(
            self.visual_reason_label
        )
        inspector_layout.addWidget(
            self.visual_prompt_edit
        )
        inspector_layout.addLayout(
            preview_row
        )

        visual_layout.addLayout(
            visual_header
        )
        visual_layout.addLayout(
            image_status_row
        )
        visual_layout.addLayout(
            model_row
        )
        visual_layout.addWidget(
            self.visual_status_label
        )
        visual_layout.addWidget(
            self.visual_slots_list
        )
        visual_layout.addWidget(
            self.visual_inspector
        )

        transcript_frame = QFrame()
        transcript_frame.setObjectName("Panel")
        transcript_layout = QVBoxLayout(transcript_frame)
        transcript_layout.setContentsMargins(16, 16, 16, 16)
        transcript_layout.setSpacing(10)

        transcript_header = QHBoxLayout()
        transcript_title = QLabel("TRANSCRIPT SCRAP")
        transcript_title.setObjectName("SectionTitle")

        self.transcript_status_label = QLabel("Run Find Best Clips to load the source transcript.")
        self.transcript_status_label.setObjectName("TranscriptStatus")

        transcript_header.addWidget(transcript_title)
        transcript_header.addSpacing(8)
        transcript_header.addWidget(self.transcript_status_label, 1)

        transcript_actions = QHBoxLayout()
        transcript_actions.setSpacing(8)

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

        transcript_actions.addWidget(self.edit_transcript_button)
        transcript_actions.addWidget(self.reset_transcript_text_button)
        transcript_actions.addStretch()
        transcript_actions.addWidget(self.cut_transcript_button)
        transcript_actions.addWidget(self.restore_transcript_button)

        self.transcript_list = QListWidget()
        self.transcript_list.setObjectName("TranscriptList")
        self.transcript_list.setAlternatingRowColors(False)
        self.transcript_list.itemClicked.connect(self.transcript_item_clicked)
        self.transcript_list.itemDoubleClicked.connect(
            self.edit_transcript_item
        )

        transcript_layout.addLayout(transcript_header)
        transcript_layout.addLayout(transcript_actions)
        transcript_layout.addWidget(self.transcript_list, 1)

        ai_scroll = QScrollArea()
        ai_scroll.setObjectName("PanelScroll")
        ai_scroll.setWidgetResizable(True)
        ai_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ai_scroll.setWidget(ai_frame)

        visual_scroll = QScrollArea()
        visual_scroll.setObjectName("PanelScroll")
        visual_scroll.setWidgetResizable(True)
        visual_scroll.setFrameShape(QFrame.Shape.NoFrame)
        visual_scroll.setWidget(visual_frame)

        right_column.addWidget(ai_scroll)
        right_column.addWidget(visual_scroll)
        right_column.addWidget(transcript_frame)
        right_column.setSizes([180, 360, 520])

        workspace.addWidget(right_column)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        workspace.setStretchFactor(2, 0)
        workspace.setSizes([280, 760, 440])

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

    def eventFilter(
        self,
        watched,
        event,
    ):

        overlay = getattr(
            self,
            "ai_visual_preview_overlay",
            None,
        )
        video_widget = getattr(
            self,
            "video_widget",
            None,
        )

        # QVideoWidget can use a native video surface on Windows, so mouse
        # events do not always arrive on the child QLabel overlay. Accept the
        # drag from either the overlay itself or the video widget when the
        # pointer is over the visible image geometry, then keep tracking by
        # global mouse position until release.
        if (
            overlay is not None
            and event.type()
            == QEvent.Type.MouseButtonPress
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            should_begin = watched is overlay
            if watched is video_widget and overlay.isVisible():
                try:
                    should_begin = overlay.geometry().contains(
                        event.position().toPoint()
                    )
                except Exception:
                    should_begin = False

            if should_begin and self.begin_visual_preview_drag(
                event
            ):
                event.accept()
                return True

        if (
            self.visual_preview_dragging
            and event.type() == QEvent.Type.MouseMove
        ):
            self.update_visual_preview_drag(
                event
            )
            event.accept()
            return True

        if (
            self.visual_preview_dragging
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            self.finish_visual_preview_drag()
            event.accept()
            return True

        if (
            watched is video_widget
            and event.type() == QEvent.Type.Resize
            and hasattr(self, "ai_visual_preview_overlay")
        ):
            QTimer.singleShot(
                0,
                lambda: self.update_ai_visual_preview_overlay(
                    self.player.position()
                ),
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

            selected_visual = self.selected_visual_slot()
            if selected_visual is not None:
                visual_index = self.selected_visual_slot_index
                if visual_index is not None:
                    visual_clip_id = self.visual_clip_id(
                        selected_visual,
                        visual_index,
                    )
                    if str(
                        self.timeline.selected_asset_clip_id
                        or ""
                    ) == visual_clip_id:
                        self.delete_selected_visual()
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
            ("right_splitter", "right_splitter"),
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
            if splitter is not None and state:
                splitter.restoreState(
                    state
                )

    def save_layout_settings(self):

        for key, splitter_name in (
            ("main_splitter", "main_splitter"),
            ("right_splitter", "right_splitter"),
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
