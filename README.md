# PPG monitor

Desktop dashboard for the Seeed XIAO ESP32C3 PPG lab board. Built with
PySide6 and PyQtGraph; signal processing uses NumPy and SciPy.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

The app starts with a live, clearly labeled simulation. Select a serial port
(or type its path), then click **Connect USB**. Close other serial viewers first.
The firmware in `main.c` samples LEDs one at a time (off → red → infrared →
green), with all other LEDs off, and sends eight-column CSV at 115200 baud:

```text
time_ms,off,red,ir,red_minus_off,ir_minus_off,green,green_minus_off
```

Green is controlled by D4; its raw and ambient-subtracted readings are appended
after the original six columns. Reflash the updated firmware before connecting:
both viewers now expect all eight fields and display three separate channel plots.
The extra illumination slot reduces the per-channel sampling rate at the same ADC
settings; check the displayed rate with the hardware. Existing checks reject rates
at or below 12 samples/s and gaps above 250 ms.

Use **Disconnect**, **Start demo / Pause demo**, and **Save snapshot** inside the app.
Optional startup arguments are still supported:

```sh
.venv/bin/python main.py --port /dev/cu.YOUR_PORT
.venv/bin/python main.py --save /tmp/ppg-monitor.png
```

For a headless snapshot, add `QT_QPA_PLATFORM=offscreen` before the command.
`index.html` is also available as a standalone Web Serial viewer.

## Measurements and calibration

At least eight seconds of contiguous data is required. The Python pipeline
resamples onto a uniform grid, applies a 0.5–5 Hz second-order Butterworth
bandpass forward and backward, and excludes one second at each edge.
Heart rate comes from the median interval between infrared peaks, with
subsample peak interpolation. Low rates, gaps, clipping, weak pulses,
channel disagreement, and irregular pulse timing suppress derived results.
Readings clear after two seconds without fresh serial data or on disconnect.
These heuristics cannot detect all motion artifacts.
The green channel is acquired and plotted; it is not used in the existing
infrared heart-rate algorithm or red/infrared SpO₂ ratio. Clipping checks cover
all four raw ADC readings (off, red, infrared, and green).

R = (red AC / red DC) / (infrared AC / infrared DC). AC is the filtered
standard deviation; DC is the absolute unfiltered mean over the same interior
window. SpO₂ remains blank until **Use my calibration** is enabled and both
coefficients are entered: `SpO₂ (%) = intercept + slope × R`. Alternatively,
pass `--calibration INTERCEPT SLOPE`. No coefficients are assumed or saved.
Values outside 0–100% are rejected. Coefficients must be established for the
actual sensor and processing; the browser uses a different filter design.
These are experimental lab estimates, not validated medical measurements.

## Checks

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
node --test test-dashboard.cjs
```

AI assistance: ChatGPT helped develop the firmware, Python and HTML interfaces,
simulation, signal processing, tests, and documentation.
