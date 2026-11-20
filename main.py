"""Native PPG dashboard using PySide6, PyQtGraph, and SciPy.

AI assistance: ChatGPT drafted the desktop interface and simulation.
"""
import argparse
import sys
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as Qt
import pyqtgraph as pg
import serial
from serial.tools import list_ports

from ppg import Reading, Samples, analyze, demo_row

INK, MUTED, RED, BLUE = '#183246', '#607789', '#d64f63', '#217c9b'
GREEN = '#25865a'
CHANNELS = [('Red channel', RED, 4, 18000, 220),
            ('Infrared channel', BLUE, 5, 25000, 500),
            ('Green channel', GREEN, 7, 21000, 700)]
STYLE = """
QWidget { color: #183246; font-size: 14px; }
QMainWindow, QWidget#page { background: #f2f5f8; }
QLabel { background: transparent; }
QLabel#title { font-size: 32px; font-weight: 700; }
QLabel#muted { color: #607789; }
QLabel#badge { background: #e0ebf1; color: #225b76; border-radius: 12px; padding: 9px 16px; font-weight: 600; }
QFrame#panel, QGroupBox { background: white; border: 1px solid #dfe7ed; border-radius: 12px; }
QLabel#metric { font-size: 32px; font-weight: 700; }
QPushButton, QComboBox, QLineEdit { background: white; border: 1px solid #ccd9e2; border-radius: 6px; padding: 9px 12px; min-height: 20px; }
QPushButton:hover { background: #eaf1f6; }
QPushButton:disabled { color: #99a9b6; background: #edf1f5; }
QPushButton#primary { background: #173b50; color: white; font-weight: 600; }
QComboBox::drop-down { border: none; width: 24px; }
QGroupBox { margin-top: 10px; padding: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
QCheckBox { spacing: 8px; }
"""


def label(text='', name=None):
    widget = Qt.QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def panel():
    frame = Qt.QFrame()
    frame.setObjectName('panel')
    return frame


class Monitor(Qt.QMainWindow):
    def __init__(self, initial_port=None, calibration=None):
        super().__init__()
        self.setWindowTitle('PPG monitor')
        self.resize(1160, 920)
        self.setMinimumSize(800, 720)
        self.setStyleSheet(STYLE)
        self.samples = Samples()
        self.port = None
        self.mode = 'demo'
        self.source = 'demo'
        self.demo_ms = 0
        self.demo_clock = time.monotonic()
        self.last_received = 0
        self.next_analysis = 0
        self.reading = Reading('Collecting an 8-second window')
        self.last_row_time = None
        self._build_ui()
        self.refresh_ports()
        if calibration:
            self.intercept.setText(str(calibration[0]))
            self.slope.setText(str(calibration[1]))
            self.calibration_enabled.setChecked(True)
        if initial_port:
            self.ports.setEditText(initial_port)
            self.connect_serial()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)
        self.update_controls()

    def _build_ui(self):
        scroll = Qt.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(Qt.QFrame.Shape.NoFrame)
        self.setCentralWidget(scroll)
        page = Qt.QWidget()
        page.setObjectName('page')
        scroll.setWidget(page)
        layout = Qt.QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        header = Qt.QHBoxLayout()
        titles = Qt.QVBoxLayout()
        titles.addWidget(label('SEEED XIAO ESP32C3', 'muted'))
        titles.addWidget(label('PPG monitor', 'title'))
        titles.addWidget(label('Three optical channels. One USB connection.', 'muted'))
        header.addLayout(titles)
        header.addStretch()
        self.badge = label('SIMULATED DATA', 'badge')
        header.addWidget(self.badge, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        toolbar = panel()
        tools = Qt.QHBoxLayout(toolbar)
        tools.setContentsMargins(16, 12, 16, 12)
        self.ports = Qt.QComboBox()
        self.ports.setEditable(True)
        self.ports.setMinimumWidth(220)
        self.ports.setToolTip('Select or type the board’s serial port')
        self.ports.setAccessibleName('Serial port')
        self.refresh_button = Qt.QPushButton('Refresh ports')
        self.connect_button = Qt.QPushButton('Connect USB')
        self.connect_button.setObjectName('primary')
        self.disconnect_button = Qt.QPushButton('Disconnect')
        self.demo_button = Qt.QPushButton('Pause demo')
        for widget in (self.ports, self.refresh_button, self.connect_button,
                       self.disconnect_button, self.demo_button):
            tools.addWidget(widget)
        tools.addStretch()
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_serial)
        self.disconnect_button.clicked.connect(self.disconnect_serial)
        self.demo_button.clicked.connect(self.toggle_demo)
        layout.addWidget(toolbar)
        self.connection_status = label('Demo running. Connect your board to view real samples.', 'muted')
        self.connection_status.setWordWrap(True)
        layout.addWidget(self.connection_status)

        cards = Qt.QHBoxLayout()
        self.metrics = []
        for title, note in [('Heart rate', 'beats per minute'), ('Ratio R', 'Red AC/DC ÷ infrared AC/DC'),
                            ('SpO₂ estimate', 'Calibration required')]:
            card = panel()
            content = Qt.QVBoxLayout(card)
            content.setContentsMargins(22, 16, 22, 16)
            content.addWidget(label(title, 'muted'))
            value, detail = label('—', 'metric'), label(note, 'muted')
            detail.setWordWrap(True)
            content.addWidget(value)
            content.addWidget(detail)
            self.metrics.append((value, detail))
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        self.analysis_status = label('Collecting an 8-second window', 'muted')
        self.analysis_status.setWordWrap(True)
        layout.addWidget(self.analysis_status)

        self.plots, self.curves, self.channel_labels = [], [], []
        pg.setConfigOptions(antialias=True, background='w', foreground=MUTED)
        for title, color, _, _, _ in CHANNELS:
            card = panel()
            content = Qt.QVBoxLayout(card)
            content.setContentsMargins(16, 12, 16, 8)
            row = Qt.QHBoxLayout()
            heading = label(title)
            heading.setStyleSheet(f'color: {color}; font-weight: 600; font-size: 16px;')
            average = label('Ambient-subtracted · ADC counts', 'muted')
            row.addWidget(heading)
            row.addStretch()
            row.addWidget(average)
            content.addLayout(row)
            plot = pg.PlotWidget()
            plot.setFixedHeight(180)
            plot.setLabel('left', 'ADC counts')
            plot.setLabel('bottom', 'Time before latest sample', units='s')
            plot.setXRange(-10, 0, padding=0)
            plot.setMouseEnabled(x=False, y=False)
            plot.hideButtons()
            plot.setMenuEnabled(False)
            plot.showGrid(x=False, y=True, alpha=.14)
            plot.getAxis('left').setWidth(85)
            plot.getAxis('bottom').setTicks([[(-10, '−10'), (-8, '−8'), (-6, '−6'), (-4, '−4'), (-2, '−2'), (0, '0')]])
            curve = plot.plot(pen=pg.mkPen(color, width=2), connect='finite')
            content.addWidget(plot)
            layout.addWidget(card, 1)
            self.plots.append(plot)
            self.curves.append(curve)
            self.channel_labels.append(average)
        for plot in self.plots[1:]:
            plot.setXLink(self.plots[0])

        calibration = Qt.QGroupBox('SpO₂ calibration')
        form = Qt.QGridLayout(calibration)
        self.calibration_enabled = Qt.QCheckBox('Use my calibration')
        self.intercept, self.slope = Qt.QLineEdit(), Qt.QLineEdit()
        for field in (self.intercept, self.slope):
            field.setPlaceholderText('Your coefficient')
            field.setMaximumWidth(180)
            field.textChanged.connect(self.invalidate_analysis)
        intercept_label, slope_label = label('Intercept'), label('Slope')
        intercept_label.setBuddy(self.intercept)
        slope_label.setBuddy(self.slope)
        form.addWidget(self.calibration_enabled, 0, 0)
        form.addWidget(intercept_label, 0, 1)
        form.addWidget(self.intercept, 0, 2)
        form.addWidget(slope_label, 0, 3)
        form.addWidget(self.slope, 0, 4)
        note = label('SpO₂ (%) = intercept + slope × R. Use coefficients fitted for this sensor and processing pipeline.', 'muted')
        note.setWordWrap(True)
        form.addWidget(note, 1, 0, 1, 5)
        self.calibration_enabled.toggled.connect(self.invalidate_analysis)
        layout.addWidget(calibration)
        bottom = Qt.QHBoxLayout()
        footer = label('10-second view · Independent channel scales · Experimental lab estimates\nAI assistance: ChatGPT helped develop this application.', 'muted')
        footer.setWordWrap(True)
        bottom.addWidget(footer, 1)
        self.snapshot_button = Qt.QPushButton('Save snapshot…')
        self.snapshot_button.clicked.connect(self.save_snapshot)
        bottom.addWidget(self.snapshot_button)
        layout.addLayout(bottom)

    def invalidate_analysis(self, *_):
        self.next_analysis = 0

    def refresh_ports(self):
        current = self.ports.currentText()
        self.ports.clear()
        try:
            ports = list(list_ports.comports())
        except (OSError, serial.SerialException) as error:
            self.connection_status.setText(f'Could not list ports: {error}')
            ports = []
        for info in ports:
            self.ports.addItem(info.device)
            self.ports.setItemData(self.ports.count() - 1, info.description, QtCore.Qt.ItemDataRole.ToolTipRole)
        if current:
            self.ports.setEditText(current)
        elif not ports:
            self.ports.setEditText('')
            self.ports.lineEdit().setPlaceholderText('Select or type serial port')

    def update_controls(self):
        connected = self.port is not None
        self.connect_button.setEnabled(not connected)
        self.disconnect_button.setEnabled(connected)
        self.ports.setEnabled(not connected)
        self.refresh_button.setEnabled(not connected)
        self.demo_button.setEnabled(not connected)
        self.demo_button.setText('Pause demo' if self.mode == 'demo' else 'Start demo')
        self.badge.setText('SIMULATED DATA' if self.mode == 'demo' else 'USB SERIAL' if connected else
                           'PAUSED DEMO' if self.source == 'demo' else 'DISCONNECTED')

    def connect_serial(self):
        device = self.ports.currentText().strip()
        if not device:
            self.connection_status.setText('Select or type a serial port first.')
            return
        try:
            port = serial.Serial(device, 115200, timeout=0, write_timeout=0)
        except (serial.SerialException, OSError, ValueError) as error:
            self.connection_status.setText(f'Could not connect: {error}. Close other serial viewers and retry.')
            return
        self.port = port
        self.mode = self.source = 'serial'
        self.samples.clear()
        self.last_row_time = None
        self.last_received = time.monotonic()
        self.next_analysis = 0
        self.connection_status.setText(f'Connected to {device} at 115200 baud. Waiting for CSV samples…')
        self.update_controls()
        self.update_readings(time.monotonic())

    def disconnect_serial(self, message='USB disconnected. Reconnect or start the demo.'):
        if self.port:
            try:
                self.port.close()
            except (OSError, serial.SerialException):
                pass
        self.port = None
        self.mode = 'paused'
        self.next_analysis = 0
        self.connection_status.setText(message)
        self.update_controls()
        self.update_readings(time.monotonic())

    def toggle_demo(self):
        if self.mode == 'demo':
            self.mode = 'paused'
            self.connection_status.setText('Demo paused. Displaying the last simulated samples.')
        else:
            self.samples.clear()
            self.mode = self.source = 'demo'
            self.demo_ms = 0
            self.demo_clock = time.monotonic()
            self.last_row_time = None
            self.connection_status.setText('Demo running. These signals and readings are simulated.')
        self.next_analysis = 0
        self.update_controls()

    def calibration(self):
        if not self.calibration_enabled.isChecked():
            return None
        try:
            values = (float(self.intercept.text()), float(self.slope.text()))
        except ValueError:
            return (float('nan'), float('nan'))
        return values

    def update_readings(self, now):
        if self.source == 'serial' and self.port is None:
            reading = Reading('USB disconnected')
        elif self.mode == 'serial' and now - self.last_received > 2:
            reading = Reading('No recent samples. Check the firmware and USB connection.')
        else:
            reading = analyze(self.samples.rows, self.calibration())
        self.reading = reading
        self.metrics[0][0].setText('—' if reading.bpm is None else f'{reading.bpm:.1f}')
        self.metrics[1][0].setText('—' if reading.ratio is None else f'{reading.ratio:.3f}')
        self.metrics[2][0].setText('—' if reading.oxygen is None else f'{reading.oxygen:.1f}%')
        calibration = self.calibration()
        oxygen_note = reading.oxygen_note
        if calibration is not None and not np.isfinite(calibration).all():
            oxygen_note = 'Enter two valid coefficients'
        elif calibration is not None and reading.ratio is None:
            oxygen_note = 'Waiting for a stable signal'
        self.metrics[2][1].setText(oxygen_note)
        prefix = ('Simulated · ' if self.mode == 'demo' else 'Paused demo · ') if self.source == 'demo' else ''
        rate = f' · {reading.sample_rate:.1f} samples/s' if reading.sample_rate else ''
        self.analysis_status.setText(prefix + reading.status + rate)
        self.next_analysis = now + .25

    def tick(self):
        now = time.monotonic()
        if self.mode == 'demo':
            count = min(40, int((now - self.demo_clock) / .015))
            for _ in range(count):
                self.demo_ms += 15
                self.samples.append(demo_row(self.demo_ms))
            self.demo_clock = now if count == 40 else self.demo_clock + count * .015
        elif self.port is not None:
            try:
                chunk = self.port.read(min(self.port.in_waiting, 65536))
                if chunk and self.samples.feed(chunk):
                    self.last_received = now
                    self.connection_status.setText(f'Receiving USB samples from {self.port.port} · 115200 baud')
            except (OSError, serial.SerialException) as error:
                self.disconnect_serial(f'USB connection lost: {error}')
        if self.samples.rows:
            latest = self.samples.rows[-1][0]
            if self.last_row_time is not None and latest < self.last_row_time:
                self.next_analysis = 0
            self.last_row_time = latest
            data = np.asarray(self.samples.rows, dtype=float)
            advance = max(0, (now - self.demo_clock) * 1000) if self.mode == 'demo' else 0
            times = (data[:, 0] - latest - advance) / 1000
            # Break plot lines at gaps rather than drawing across missing data.
            gaps = np.flatnonzero(np.diff(data[:, 0]) > 250) + 1
            plot_times = np.insert(times, gaps, np.nan)
            for i, (curve, plot) in enumerate(zip(self.curves, self.plots)):
                _, _, column, center, amplitude = CHANNELS[i]
                values = data[:, column]
                curve.setData(plot_times, np.insert(values, gaps, np.nan))
                self.channel_labels[i].setText(f'Mean {np.mean(values):,.0f} ADC counts')
                if self.source == 'demo':
                    plot.setYRange(center - 1.6 * amplitude, center + 1.6 * amplitude, padding=0)
                else:
                    plot.enableAutoRange(axis='y', enable=True)
        else:
            for curve in self.curves:
                curve.setData([], [])
            for heading in self.channel_labels:
                heading.setText('Ambient-subtracted · ADC counts')
        if now >= self.next_analysis:
            self.update_readings(now)

    def save_snapshot(self):
        path, _ = Qt.QFileDialog.getSaveFileName(self, 'Save dashboard snapshot', 'ppg-monitor.png', 'PNG image (*.png)')
        if path and not self.centralWidget().widget().grab().save(path, 'PNG'):
            self.connection_status.setText('Could not save the snapshot. Check the destination folder.')

    def closeEvent(self, event):
        self.timer.stop()
        if self.port:
            try:
                self.port.close()
            except (OSError, serial.SerialException):
                pass
        event.accept()


def main():
    parser = argparse.ArgumentParser(description='PPG desktop dashboard (PySide6 + PyQtGraph)')
    parser.add_argument('--port', help='Connect to this serial port at startup; otherwise show a live demo.')
    parser.add_argument('--calibration', type=float, nargs=2, metavar=('INTERCEPT', 'SLOPE'))
    parser.add_argument('--save', help='Save a simulated dashboard snapshot as a PNG and exit.')
    args = parser.parse_args()
    if args.port and args.save:
        parser.error('--save is available in simulation mode')
    if args.calibration and not np.isfinite(args.calibration).all():
        parser.error('calibration coefficients must be finite')
    app = Qt.QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setFont(QtGui.QFont('Arial', 11))
    window = Monitor(args.port, args.calibration)
    window.show()
    if args.save:
        window.timer.stop()
        for ms in range(0, 12001, 15):
            window.samples.append(demo_row(ms))
        window.mode = 'paused'
        window.connection_status.setText('Demo paused. Snapshot of simulated signals and readings.')
        window.update_controls()
        window.tick()
        app.processEvents()
        if not window.centralWidget().widget().grab().save(args.save, 'PNG'):
            parser.exit(1, f'Could not save snapshot to {args.save}\n')
        print(f'Heart rate {window.reading.bpm:.1f} bpm | R {window.reading.ratio:.3f}')
        window.close()
        return
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
