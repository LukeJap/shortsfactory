from gui_app.style import STYLESHEET


def test_standard_dialogs_use_dark_readable_surfaces():
    assert "QMessageBox," in STYLESHEET
    assert "QInputDialog {" in STYLESHEET
    assert "background-color: #121216;" in STYLESHEET
    assert "QMessageBox QLabel#qt_msgbox_label" in STYLESHEET
    assert "color: #F2ECE4;" in STYLESHEET


def test_dialog_controls_have_consistent_button_and_input_states():
    assert "QMessageBox QPushButton" in STYLESHEET
    assert "QDialogButtonBox QPushButton:hover" in STYLESHEET
    assert "QInputDialog QPushButton:pressed" in STYLESHEET
    assert "QInputDialog QLineEdit" in STYLESHEET
    assert "QInputDialog QDoubleSpinBox" in STYLESHEET
    assert "selection-background-color: #741C28;" in STYLESHEET


def test_input_dialogs_do_not_restore_the_old_white_surface():
    dialog_rules = STYLESHEET[
        STYLESHEET.index("QDialog,") : STYLESHEET.index("QLabel#EmojiPreviewOverlay:hover")
    ]
    assert "background: #FFFFFF" not in dialog_rules
    assert "color: #000000" not in dialog_rules
