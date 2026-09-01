"""
The application's entire QSS stylesheet as a single string (STYLESHEET),
applied once to the whole app in main_window.py's apply_style(). Dark/
"gothic-industrial" theme: near-black panels (#09090A/#101012/#0A0A0B),
off-white/cream text (#DED6C8), blood-red/rust accents (#741C28/#C9384F/
#733B2D), muted grey secondary text (#B8AEA1/#918B84/#7E7670), sharp
(not rounded) corners. See SHORTSFACTORY.md's Project Context section for
the full visual-identity writeup.
"""

from __future__ import annotations

STYLESHEET =            """
            QMainWindow {
                background: #09090A;
            }

            QWidget {
                color: #DED6C8;
                font-family: Segoe UI;
                font-size: 13px;
            }

            QFrame#HeaderPanel {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #101012, stop:0.58 #17171A, stop:1 #210D12);
                border: 1px solid #3B2227;
                border-left: 4px solid #741C28;
                border-radius: 5px;
            }

            QFrame#Panel, QFrame#PreviewPanel, QFrame#RenderLogPanel, QFrame#SubPanel {
                background: #101012;
                border: 1px solid #252429;
                border-radius: 5px;
            }

            QFrame#PreviewPanel {
                border: 1px solid #40333A;
                border-top: 2px solid #733B2D;
            }

            QFrame#RenderLogPanel {
                background: #0C0C0E;
                border-top: 2px solid #3A3030;
            }

            QFrame#SubPanel {
                background: #0A0A0B;
                border: 1px solid #252429;
                border-radius: 4px;
            }

            QSplitter::handle {
                background: #09090A;
                border: 1px solid #252429;
            }

            QSplitter::handle:horizontal {
                width: 8px;
            }

            QSplitter::handle:hover {
                background: #741C28;
                border: 1px solid #C9384F;
            }

            QScrollArea#PanelScroll {
                background: transparent;
                border: none;
            }

            QScrollArea#CenterScroll {
                background: transparent;
                border: none;
            }

            QScrollArea#RightEditorScroll {
                background: transparent;
                border: none;
            }

            QLabel#AppTitle {
                font-size: 31px;
                font-weight: 900;
                letter-spacing: 0px;
                color: #DED6C8;
            }

            QLabel#AppSubtitle {
                color: #C9384F;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 2px;
            }

            QLabel#ModeBadge, QLabel#MicroBadge {
                color: #DED6C8;
                background: #160B0E;
                border: 1px solid #741C28;
                border-radius: 3px;
                padding: 6px 10px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1px;
            }

            QLabel#SectionTitle {
                color: #B8AEA1;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 2px;
                text-transform: uppercase;
            }

            QLabel#HintLabel {
                color: #918B84;
                font-size: 11px;
                line-height: 1.4;
            }

            QLabel#TinyLabel {
                color: #7E7670;
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QFrame#DropZone {
                background: #0A0A0B;
                border: 1px dashed #5A3433;
                border-radius: 5px;
            }

            QFrame#DropZone:hover {
                background: #121418;
                border: 1px dashed #d04b5f;
            }

            QFrame#EditStylePanel {
                background: #0B0B0D;
                border: 1px solid #30292D;
                border-left: 3px solid #741C28;
                border-radius: 4px;
            }

            QLabel#DropIcon {
                font-size: 44px;
                color: #d04b5f;
            }

            QLabel#DropTitle {
                font-size: 18px;
                font-weight: 800;
                color: #f2eee8;
            }

            QLabel#DropSubtitle {
                color: #7b7370;
                font-size: 11px;
                letter-spacing: 1px;
            }

            QLabel#FileLabel, QLabel#MusicLabel {
                color: #D2C8BA;
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 9px 11px;
            }

            QLabel#SelectionLabel {
                color: #f4f0ea;
                font-weight: 700;
                padding-left: 4px;
            }

            QLabel#SuggestionLabel {
                color: #9a8f88;
                font-size: 11px;
                padding: 0px 2px 0px 2px;
            }

            QLabel#TrimHelp {
                color: #756d68;
                font-size: 10px;
                font-family: Consolas;
                letter-spacing: 1px;
            }

            QLabel#TranscriptStatus, QLabel#MusicVolumeLabel, QLabel#TimeLabel {
                color: #968b86;
                font-size: 11px;
            }

            QListWidget#TranscriptList {
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 6px;
                color: #D3CBBF;
                outline: none;
                font-size: 12px;
            }

            QListWidget#TranscriptList::item {
                border-radius: 3px;
                padding: 8px 10px;
                margin: 2px 0px;
            }

            QListWidget#TranscriptList::item:hover {
                background: #181216;
                color: #ffffff;
            }

            QListWidget#TranscriptList::item:selected {
                background: #241016;
                border: 1px solid #C9384F;
                color: #FFF3E3;
            }

            QScrollBar:vertical, QScrollBar:horizontal {
                background: #09090A;
                border: 1px solid #1C1B1F;
                margin: 0px;
            }

            QScrollBar:vertical {
                width: 10px;
            }

            QScrollBar:horizontal {
                height: 10px;
            }

            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #3A3637;
                border: 1px solid #5A3433;
                border-radius: 2px;
                min-height: 24px;
                min-width: 24px;
            }

            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background: #741C28;
                border: 1px solid #C9384F;
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0px;
                height: 0px;
            }

            QScrollBar::add-page, QScrollBar::sub-page {
                background: transparent;
            }

            QComboBox#CompactCombo, QLineEdit#CompactLineEdit, QSpinBox#CompactSpinBox {
                color: #ded7cf;
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 3px;
                padding: 6px 8px;
                min-height: 24px;
            }

            QLabel#RecapStatus {
                color: #c7c0b7;
                background: #0b0d0f;
                border: 1px solid #242226;
                border-radius: 4px;
                padding: 7px 9px;
                font-size: 10px;
                font-weight: 800;
            }

            QComboBox#CompactCombo:disabled,
            QLineEdit#CompactLineEdit:disabled,
            QSpinBox#CompactSpinBox:disabled {
                color: #625b58;
                background: #101114;
                border: 1px solid #201d20;
            }

            QTextEdit#PromptEdit {
                font-family: Segoe UI;
                font-size: 11px;
                color: #d8d0c8;
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 3px;
                padding: 8px;
            }

            QInputDialog QTextEdit,
            QInputDialog QPlainTextEdit,
            QInputDialog QLineEdit {
                color: #000000;
                background: #FFFFFF;
                selection-color: #000000;
                selection-background-color: #B8D7FF;
            }

            QInputDialog QComboBox,
            QInputDialog QListView,
            QInputDialog QAbstractItemView {
                color: #F2ECE4;
                background: #09090A;
                border: 1px solid #5D252E;
                selection-color: #FFFFFF;
                selection-background-color: #6E1E2B;
            }

            QLabel#EmojiPreviewOverlay:hover,
            QLabel#CaptionPreviewOverlay:hover {
                border: 2px solid #C9384F;
            }

            QLabel#PersistentTitlePreview {
                background: transparent;
                color: #FFFFFF;
                border: none;
            }

            QLineEdit#PersistentTitleInput {
                background: #0A0A0B;
                border: 1px solid #40333A;
                border-radius: 3px;
                color: #F4EFE6;
                font-weight: 700;
                padding: 5px 7px;
            }

            QCheckBox#UiPreviewToggle {
                color: #B8AEA1;
                font-size: 10px;
                font-weight: 800;
            }

            QCheckBox#UiPreviewToggle::indicator {
                width: 14px;
                height: 14px;
            }

            QLabel#EmojiResizeHandle,
            QLabel#CaptionResizeHandle {
                background: #C9384F;
                border: 1px solid #F4EFE6;
                border-radius: 2px;
            }

            QLabel#EmojiResizeReadout,
            QLabel#CaptionResizeReadout {
                background: #741C28;
                color: #F4EFE6;
                border: 1px solid #C9384F;
                border-radius: 4px;
                font-size: 10px;
                font-weight: 900;
                padding: 2px 6px;
            }

            QVideoWidget#VideoPreview {
                background: #020203;
                border: 1px solid #2e272b;
                border-radius: 5px;
            }

            QWidget#ProgramMonitorViewport {
                background: #080809;
                border: 1px solid #252429;
                border-radius: 5px;
            }

            QTextEdit#RenderLog {
                background: #070708;
                color: #CFC8C1;
                border: 1px solid #252429;
                border-radius: 3px;
                font-family: Consolas;
                font-size: 10px;
            }

            QWidget#TimelinePanel {
                background: #080809;
                border: 1px solid #2E2927;
            }

            QFrame#TimelineFooter {
                background: #0B0B0D;
                border-top: 1px solid #2A2527;
            }

            QLabel#TimelineLegendItem {
                background: #151417;
                border: 1px solid #302A2E;
                border-radius: 3px;
                color: #D8D1C8;
                font-family: Consolas;
                font-size: 9px;
                font-weight: 700;
                padding: 3px 5px;
            }

            QLabel#TimelineLegendItem[tone="cyan"] { border-left: 3px solid #37C8E5; }
            QLabel#TimelineLegendItem[tone="red"] { border-left: 3px solid #D64959; }
            QLabel#TimelineLegendItem[tone="amber"] { border-left: 3px solid #D58B3A; }
            QLabel#TimelineLegendItem[tone="gold"] { border-left: 3px solid #E3BE57; }
            QLabel#TimelineLegendItem[tone="violet"] { border-left: 3px solid #9B71DB; }
            QLabel#TimelineLegendItem[tone="magenta"] { border-left: 3px solid #D25AA6; }
            QLabel#TimelineLegendItem[tone="green"] { border-left: 3px solid #5CAA74; }

            QWidget#VideoStack {
                background: transparent;
            }

            QPushButton {
                background: #17171A;
                color: #DED6C8;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 10px 16px;
                font-weight: 700;
            }

            QPushButton:hover {
                background: #212329;
                border: 1px solid #5a434c;
            }

            QPushButton:pressed {
                background: #111216;
            }

            QPushButton#PlayButton {
                min-width: 74px;
                max-width: 74px;
                min-height: 38px;
                padding: 4px 10px;
                background: #190B10;
                border: 1px solid #741C28;
                border-radius: 4px;
                color: #FFF0E8;
                font-size: 11px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QPushButton#TinyButton {
                color: #DED6C8;
                background: #101012;
                border: 1px solid #3A3030;
                border-radius: 3px;
                padding: 5px 9px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QPushButton#TinyButton:hover {
                background: #1C1014;
                border: 1px solid #C9384F;
            }

            QPushButton#EditStyleButton {
                color: #968B86;
                background: #101012;
                border: 1px solid #30292D;
                border-radius: 4px;
                padding: 7px 3px;
                min-height: 38px;
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QPushButton#EditStyleButton:hover {
                color: #F2E6D4;
                background: #1A1115;
                border: 1px solid #8D3445;
            }

            QPushButton#EditStyleButton:checked {
                color: #FFF3E3;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4E1520, stop:1 #741C28);
                border: 1px solid #E05C6F;
            }

            QPushButton#AutoCutsToggle,
            QPushButton#FiltersToggle,
            QPushButton#EmojiToggle {
                color: #968B86;
                background: #101012;
                border: 1px solid #30292D;
                border-radius: 4px;
                padding: 8px 10px;
                min-height: 16px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
                text-align: left;
            }

            QPushButton#AutoCutsToggle:hover,
            QPushButton#FiltersToggle:hover,
            QPushButton#EmojiToggle:hover {
                color: #F2E6D4;
                background: #1A1115;
                border: 1px solid #8D3445;
            }

            QPushButton#AutoCutsToggle:checked,
            QPushButton#FiltersToggle:checked,
            QPushButton#EmojiToggle:checked {
                color: #FFF3E3;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4E1520, stop:1 #741C28);
                border: 1px solid #E05C6F;
            }

            QPushButton#CutButton {
                color: #ffd8dd;
                background: #2a141a;
                border: 1px solid #8d3445;
                padding: 7px 10px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QPushButton#CutButton:hover {
                background: #3a1821;
                border: 1px solid #e4586d;
            }

            QPushButton#RestoreButton {
                color: #c6c0b9;
                background: #131518;
                border: 1px solid #373136;
                padding: 7px 10px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1px;
            }

            QPushButton#RestoreButton:hover {
                color: #ffffff;
                background: #1b1d21;
                border: 1px solid #695a62;
            }

            QPushButton#MusicButton, QPushButton#AIButton {
                color: #ffd8dd;
                background: #1b1417;
                border: 1px solid #5f3740;
            }

            QPushButton#MusicButton:hover, QPushButton#AIButton:hover {
                background: #26191e;
                border: 1px solid #d04b5f;
            }

            QPushButton#QuietButton {
                color: #9d9089;
                background: #131417;
                border: 1px solid #282327;
            }

            QPushButton#QuietButton:disabled {
                color: #5d5755;
                background: #101114;
                border: 1px solid #201d20;
            }

            QPushButton#ClipCard {
                background: #09090A;
                color: #D1C7C0;
                border: 1px solid #252429;
                border-left: 3px solid #4B3657;
                border-radius: 4px;
                padding: 11px 12px;
                text-align: left;
                font-size: 11px;
                font-weight: 700;
            }

            QPushButton#ClipCard:hover {
                background: #171317;
                border: 1px solid #6b3f49;
                color: #fff8f2;
            }

            QPushButton#ClipCard[selected="true"] {
                background: #1A0D12;
                color: #FFF3E3;
                border: 1px solid #C9384F;
                border-left: 3px solid #C9384F;
            }

            QPushButton#GenerateButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #741C28, stop:1 #C9384F);
                color: white;
                border: 1px solid #E05C6F;
                border-radius: 4px;
                font-weight: 900;
                padding: 12px 20px;
            }

            QPushButton#GenerateButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #a12740, stop:1 #ef5d74);
            }

            QPushButton#GenerateButton:disabled {
                background: #29242a;
                border: 1px solid #3b3238;
                color: #6e676a;
            }

            QTextEdit {
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 10px;
                color: #cfc8c1;
                selection-background-color: #5e2631;
                font-family: Consolas;
                font-size: 11px;
            }

            QFrame#GlobalProgressPanel {
                background: #0B0B0D;
                border: 1px solid #30292D;
                border-top: 2px solid #741C28;
                border-radius: 4px;
            }

            QLabel#RenderProgressStage {
                color: #F2E6D4;
                background: #09090A;
                border: 1px solid #30292D;
                border-left: 3px solid #C9384F;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QLabel#RenderProgressTime {
                color: #AFA59B;
                font-family: Consolas;
                font-size: 11px;
                font-weight: 700;
            }

            QProgressBar#RenderProgressBar {
                background: #070708;
                border: 1px solid #30292D;
                border-radius: 3px;
                height: 16px;
            }

            QProgressBar#RenderProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #741C28, stop:0.55 #C9384F, stop:1 #F0A85A);
                border-radius: 2px;
            }

            QSlider::groove:horizontal {
                background: #17171A;
                height: 7px;
                border-radius: 2px;
            }

            QSlider::handle:horizontal {
                background: #DED6C8;
                border: 2px solid #C9384F;
                width: 16px;
                margin: -6px 0;
                border-radius: 3px;
            }

            QSlider::handle:horizontal:hover {
                background: #fff2f4;
            }

            QSlider::sub-page:horizontal {
                background: #741C28;
                border-radius: 2px;
            }

            QSlider::groove:horizontal:disabled {
                background: #101012;
            }

            QSlider::handle:horizontal:disabled {
                background: #4A4540;
                border: 2px solid #3A3436;
            }

            QSlider::sub-page:horizontal:disabled {
                background: #3A2226;
            }

            QWidget#TimelineNavigator {
                background: #070708;
                border: 1px solid #242020;
            }

            QSlider#TimelineZoom::handle:horizontal {
                width: 12px;
            }
            """
