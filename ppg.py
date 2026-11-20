"""Acquisition helpers and SciPy analysis, independent of the desktop UI.

AI assistance: ChatGPT drafted the acquisition helpers and signal processing.
"""
from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt


@dataclass
class Reading:
    status: str
    bpm: float | None = None
    ratio: float | None = None
    oxygen: float | None = None
    oxygen_note: str = "Calibration required"
    sample_rate: float | None = None


class Samples:
    def __init__(self):
        self.rows = deque(maxlen=4096)
        self.pending = b""

    def clear(self):
        self.rows.clear()
        self.pending = b""

    def append(self, row):
        if self.rows and row[0] <= self.rows[-1][0]:
            self.rows.clear()  # Board reset or millis() rollover.
        self.rows.append(row)
        while self.rows and self.rows[0][0] < row[0] - 10000:
            self.rows.popleft()

    def feed(self, chunk):
        """Return accepted CSV record count; tolerate partial lines and headers."""
        complete = (self.pending + chunk).split(b"\n")
        self.pending = complete.pop()
        if len(self.pending) > 1024:
            self.pending = b""
        accepted = 0
        for record in complete:
            if len(record) > 1024:
                continue
            try:
                row = tuple(map(int, record.split(b",")))
            except ValueError:
                continue
            if (len(row) == 8 and 0 <= row[0] <= 0xFFFFFFFF
                    and all(abs(value) <= 2**31 for value in row[1:])):
                self.append(row)
                accepted += 1
        return accepted


def demo_row(ms):
    phase = 2 * np.pi * 1.2 * ms / 1000
    pulse = np.sin(phase) + .12 * np.sin(2 * phase + .7)
    red, ir = 18000 + 220 * pulse, 25000 + 500 * pulse
    green = 21000 + 700 * pulse
    return (ms, 4000000, 4000000 + red, 4000000 + ir, red, ir,
            4000000 + green, green)


def analyze(rows, calibration=None):
    result = Reading("Waiting for samples")
    if len(rows) < 2:
        return result
    data = np.asarray(rows, dtype=float)
    if data.ndim != 2 or data.shape[1] != 8 or not np.isfinite(data).all():
        result.status = "Invalid sample data"
        return result
    times = (data[:, 0] - data[-1, 0]) / 1000
    steps = np.diff(times)
    dt = float(np.median(steps))
    if np.any(steps <= 0) or dt >= 1 / 12 or np.max(steps) > .25:
        result.status = "Sample rate too low or data gap"
        return result
    result.sample_rate = 1 / dt
    if np.any(data[:, [1, 2, 3, 6]] >= 8388607) or np.any(data[:, [1, 2, 3, 6]] <= -8388608):
        result.status = "ADC clipping detected"
        return result
    duration = times[-1] - times[0]
    if duration < 8:
        result.status = f"Collecting an 8-second window ({duration:.1f} / 8.0 s)"
        return result

    grid = np.arange(times[0], 0, dt)
    channels = np.array([np.interp(grid, times, data[:, col]) for col in (4, 5)])
    sos = butter(2, [.5, 5], btype="bandpass", fs=1 / dt, output="sos")
    filtered = sosfiltfilt(sos, channels, axis=1)
    # Exclude filter edges; calculate AC and DC over the same interior window.
    edge = int(np.ceil(1 / dt))
    filtered = filtered[:, edge:-edge]
    channels = channels[:, edge:-edge]
    ac = np.std(filtered, axis=1)
    if np.any(ac < 1):
        result.status = "Pulse signal too weak"
        return result
    if abs(np.corrcoef(filtered)[0, 1]) < .7:
        result.status = "Red and infrared pulses do not agree"
        return result
    ir = filtered[1]
    peaks, _ = find_peaks(ir, distance=.30 / dt, prominence=max(1, ac[1]))
    if len(peaks) < 4:
        result.status = "Insufficient pulse peaks"
        return result
    # Refine peak times to avoid BPM quantization at lower sample rates.
    curvature = ir[peaks - 1] - 2 * ir[peaks] + ir[peaks + 1]
    offsets = np.divide(.5 * (ir[peaks - 1] - ir[peaks + 1]), curvature,
                        out=np.zeros_like(curvature), where=np.abs(curvature) > 1e-12)
    intervals = np.diff(peaks + np.clip(offsets, -.5, .5)) * dt
    bpm = float(60 / np.median(intervals))
    if not 30 <= bpm <= 200 or np.std(intervals) / np.mean(intervals) > .2:
        result.status = "Unstable pulse timing; check contact and movement"
        return result
    result.bpm = bpm
    dc = np.abs(np.mean(channels, axis=1))
    if np.any(dc < 1):
        result.status = "DC signal too small for R"
        return result
    result.ratio = float((ac[0] / dc[0]) / (ac[1] / dc[1]))
    result.status = "Pulse detected"
    if calibration is not None:
        if len(calibration) != 2 or not np.isfinite(calibration).all():
            result.oxygen_note = "Enter two valid coefficients"
        else:
            intercept, slope = calibration
            estimate = intercept + slope * result.ratio
            if np.isfinite(estimate) and 0 <= estimate <= 100:
                result.oxygen = float(estimate)
                result.oxygen_note = "Using supplied calibration"
            else:
                result.oxygen_note = "Estimate out of range"
    return result
