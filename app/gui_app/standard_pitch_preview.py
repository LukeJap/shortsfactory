"""Small, audio-only FFmpeg preview for Standard Mode pitch adjustments."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSink

from standard_audio_pitch import build_standard_audio_pitch_filter


class StandardPitchPreview:
    """Stream pitched source audio while QMediaPlayer remains the transport."""

    SAMPLE_RATE = 48_000
    CHANNELS = 2

    def __init__(self, parent):
        self._process = QProcess(parent)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._pump_audio)
        self._process.finished.connect(self._source_finished)
        self._sink: QAudioSink | None = None
        self._device = None
        self._pending = bytearray()
        self._timer = QTimer(parent)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._pump_audio)

    def start(self, source_path: Path, position_ms: int, semitones: float, volume: float) -> bool:
        pitch_filter = build_standard_audio_pitch_filter(semitones)
        if not pitch_filter or not source_path.is_file():
            return False
        self.stop()
        audio_format = QAudioFormat()
        audio_format.setSampleRate(self.SAMPLE_RATE)
        audio_format.setChannelCount(self.CHANNELS)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._sink = QAudioSink(audio_format)
        self._sink.setVolume(max(0.0, min(1.0, float(volume))))
        self._device = self._sink.start()
        self._process.setProgram("ffmpeg")
        self._process.setArguments([
            "-nostdin", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, int(position_ms) / 1000.0):.3f}",
            "-i", str(source_path), "-map", "0:a:0?", "-vn",
            "-af", pitch_filter, "-ac", str(self.CHANNELS),
            "-ar", str(self.SAMPLE_RATE), "-f", "s16le", "pipe:1",
        ])
        self._process.start()
        self._timer.start()
        return True

    def stop(self):
        self._timer.stop()
        self._pending.clear()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(50)
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
        self._sink = None
        self._device = None

    def set_volume(self, volume: float):
        if self._sink is not None:
            self._sink.setVolume(max(0.0, min(1.0, float(volume))))

    def _source_finished(self, *_args):
        self._pump_audio()
        if not self._pending:
            self._timer.stop()

    def _pump_audio(self):
        if self._process.bytesAvailable() > 0:
            self._pending.extend(bytes(self._process.readAllStandardOutput()))
        if self._device is None or not self._pending or self._sink is None:
            return
        capacity = max(0, self._sink.bytesFree())
        if not capacity:
            return
        payload = bytes(self._pending[: min(capacity, len(self._pending))])
        written = self._device.write(payload)
        if written > 0:
            del self._pending[:written]
