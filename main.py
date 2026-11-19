# AI assistance: ChatGPT drafted the Python viewer and simulation.
import argparse
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from matplotlib.ticker import FuncFormatter, MultipleLocator, MaxNLocator
from scipy.signal import butter, find_peaks, sosfiltfilt


INK = "#182b3a"
MUTED = "#647786"
RED = "#d64f63"
BLUE = "#217c9b"


def create_monitor(serial_mode):
    """Build a dashboard with independent channel scales and shared time."""
    fig = plt.figure(figsize=(12, 8), facecolor="#f4f6f8")
    fig.canvas.manager.set_window_title("PPG monitor")
    fig.text(.09, .93, "PPG monitor", fontsize=25, weight="bold", color=INK)
    fig.text(.09, .891, "Ambient-subtracted optical signals", fontsize=11, color=MUTED)
    fig.text(.94, .935, "USB SERIAL" if serial_mode else "SIMULATED DATA",
             ha="right", fontsize=9, weight="bold", color=BLUE,
             bbox=dict(boxstyle="round,pad=0.6", fc="#e3eef3", ec="none"))

    metrics = []
    for left, label, color, note in (
        (.09, "HEART RATE", INK, "Waiting for a full window"),
        (.38, "RATIO  /  R", INK, "AC/DC red ÷ AC/DC infrared"),
        (.67, "SpO₂ ESTIMATE", BLUE, "Calibration required"),
    ):
        card = fig.add_axes([left, .715, .27, .135], facecolor="white")
        card.set(xticks=[], yticks=[])
        for spine in card.spines.values():
            spine.set_visible(False)
        card.text(.065, .77, label, fontsize=9, weight="bold", color=MUTED)
        value = card.text(.065, .34, "—", fontsize=25, weight="bold", color=color)
        detail = card.text(.065, .12, note, fontsize=9, color=MUTED)
        metrics.append((value, detail))

    axes = fig.subplots(2, 1, sharex=True)
    fig.subplots_adjust(left=.09, right=.94, bottom=.14, top=.64, hspace=.46)
    lines = []
    for ax, label, color in zip(axes, ("Red channel", "Infrared channel"), (RED, BLUE)):
        ax.set_facecolor("white")
        ax.set_title(label, loc="left", fontsize=12, weight="bold", color=color, pad=12)
        ax.set(xlim=(-10, 0), ylabel="ADC counts")
        ax.yaxis.label.set(color=MUTED, fontsize=9)
        ax.yaxis.set_major_locator(MaxNLocator(4))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        ax.xaxis.set_major_locator(MultipleLocator(2))
        ax.tick_params(axis="both", colors=MUTED, labelsize=9, length=0, pad=9)
        ax.grid(axis="y", color="#e8edf1", linewidth=.8)
        ax.set_axisbelow(True)
        ax.margins(y=.22)
        for name, spine in ax.spines.items():
            spine.set_visible(name == "bottom")
            spine.set_color("#dce3e9")
        lines.append(ax.plot([], [], color=color, linewidth=1.8,
                             solid_capstyle="round")[0])
    axes[-1].set_xlabel("Time before latest sample (seconds)", color=MUTED,
                        fontsize=10, labelpad=12)
    status = fig.text(.09, .055, "Waiting for samples", color=MUTED, fontsize=10)
    fig.text(.94, .055, "10 s view · independent channel scales", ha="right",
             color=MUTED, fontsize=9)
    return fig, axes, lines, metrics, status


def update_plot(rows, axes, lines, calibration, metrics, status):
    # Clear previous measurements when a reset or invalid window arrives.
    for value, detail in metrics:
        value.set_text("—")
    metrics[0][1].set_text("Waiting for a full window")
    metrics[1][1].set_text("AC/DC red ÷ AC/DC infrared")
    metrics[2][1].set_text("Calibration required" if calibration is None else "Waiting for signal")
    if len(rows) < 2:
        for line in lines:
            line.set_data([], [])
        status.set_text("Waiting for samples")
        return

    data = np.asarray(rows)
    times = (data[:, 0] - data[-1, 0]) / 1000
    keep = times >= -10
    times, data = times[keep], data[keep]

    for ax, line, values in zip(axes, lines, data[:, 4:6].T):
        line.set_data(times, values)
        ax.relim()
        ax.autoscale_view(scalex=False)
    status.set_text("Collecting an 8-second window…")

    if len(times) < 2 or times[-1] - times[0] < 8:
        return

    steps = np.diff(times)
    dt = np.median(steps)
    if dt <= 0 or dt >= 1 / 12 or max(steps) > 0.25:
        status.set_text("Sample rate too low or data gap")
        return
    if np.any(np.abs(data[:, 1:4]) >= 8_388_607):
        status.set_text("ADC clipping detected")
        return

    # Resample to a uniform grid before applying the bandpass.
    grid = np.arange(times[0], 0, dt)
    red, ir = [np.interp(grid, times, data[:, col]) for col in (4, 5)]
    filt = butter(2, [0.5, 5], btype="bandpass", fs=1 / dt, output="sos")
    red_ac, ir_ac = [sosfiltfilt(filt, channel) for channel in (red, ir)]

    peaks, _ = find_peaks(
        ir_ac, distance=0.30 / dt, prominence=max(1, np.std(ir_ac))
    )
    hr = "unavailable"
    if len(peaks) >= 4:
        bpm = 60 / np.median(np.diff(grid[peaks]))
        hr = f"{bpm:.1f} bpm"
        metrics[0][0].set_text(f"{bpm:.1f}")
        metrics[0][1].set_text("beats per minute")
    else:
        metrics[0][1].set_text("Insufficient pulse peaks")

    # DC comes from the ambient-subtracted channels before filtering.
    dc = np.abs([np.mean(red), np.mean(ir)])
    ac = np.std([red_ac, ir_ac], axis=1)
    if np.any(dc < 1) or np.any(ac < 1):
        status.set_text(f"Heart rate {hr}  |  R unavailable")
        return

    ratio = (ac[0] / dc[0]) / (ac[1] / dc[1])
    metrics[1][0].set_text(f"{ratio:.3f}")
    oxygen = "SpO₂ calibration required"
    if calibration is not None:
        intercept, slope = calibration
        estimate = intercept + slope * ratio
        oxygen = (
            f"SpO₂ {estimate:.1f}%" if 0 <= estimate <= 100
            else "SpO₂ estimate out of range"
        )
        if 0 <= estimate <= 100:
            metrics[2][0].set_text(f"{estimate:.1f}%")
            metrics[2][1].set_text("Using supplied calibration")
        else:
            metrics[2][1].set_text("Estimate out of range")
    status.set_text(f"{1 / dt:.0f} samples/s · {times[-1] - times[0]:.1f} s of signal")
    return f"Heart rate {hr}  |  R {ratio:.3f}  |  {oxygen}"


def main():
    parser = argparse.ArgumentParser(description="Arduino PPG serial viewer")
    parser.add_argument("--port", help="Serial port. Omit to run the simulation.")
    parser.add_argument("--save", help="Save the simulated plot as a PNG.")
    parser.add_argument("--calibration", type=float, nargs=2,
                        metavar=("INTERCEPT", "SLOPE"))
    args = parser.parse_args()
    if args.port and args.save:
        parser.error("--save is available in simulation mode")

    fig, axes, lines, metrics, status = create_monitor(bool(args.port))
    rows = deque(maxlen=4096)

    if not args.port:
        rng = np.random.default_rng(42)
        for ms in range(0, 12001, 15):
            pulse = np.sin(2 * np.pi * 1.2 * ms / 1000)
            off = 4_000_000
            red = round(18_000 + 220 * pulse + rng.normal(0, 10))
            ir = round(25_000 + 500 * pulse + rng.normal(0, 12))
            rows.append((ms, off, off + red, off + ir, red, ir))
        summary = update_plot(rows, axes, lines, args.calibration, metrics, status)
        print(summary or status.get_text())
        if args.save:
            fig.savefig(args.save, dpi=160)
        else:
            plt.show()
        return

    import serial

    with serial.Serial(args.port, 115200, timeout=0) as port:
        pending = b""

        def refresh(_):
            nonlocal pending
            pending += port.read(min(port.in_waiting, 65536))
            *complete, pending = pending.split(b"\n")
            if len(pending) > 1024:
                pending = b""

            for record in complete:
                try:
                    row = tuple(map(int, record.split(b",")))
                except ValueError:
                    continue  # CSV header or firmware status message
                if len(row) == 6:
                    if rows and row[0] <= rows[-1][0]:
                        rows.clear()  # Arduino reset or millis() rollover
                    rows.append(row)

            if complete:
                update_plot(rows, axes, lines, args.calibration, metrics, status)

        animation = FuncAnimation(fig, refresh, interval=250, cache_frame_data=False)
        plt.show()


if __name__ == "__main__":
    main()
