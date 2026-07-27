"""Usage history for claudeink.

Appends one JSONL line per successful poll and serves time-range queries
for the web ui. Stdlib only; the file lives beside the credentials-sized
data this project already keeps, and 30 days at a 2-minute cadence is a
couple of megabytes.

Line format: {"t": <epoch seconds>, "v": {"<label>": <percent>, ...}}
"""

import json
import os
import threading
import time
from pathlib import Path

HISTORY_FILE = Path(
    os.environ.get("HISTORY_FILE", str(Path(__file__).resolve().parent / "history.jsonl"))
)
HISTORY_DAYS = max(1, int(os.environ.get("HISTORY_DAYS", "30")))

_lock = threading.Lock()
_samples = []  # [(t, {label: pct}), ...] in time order
_loaded = False


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    cutoff = time.time() - HISTORY_DAYS * 86400
    try:
        with HISTORY_FILE.open() as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    if row["t"] >= cutoff:
                        _samples.append((row["t"], row["v"]))
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        return
    _samples.sort(key=lambda s: s[0])


def _compact():
    """Rewrite the file without expired rows (runs at most once a day)."""
    tmp = HISTORY_FILE.with_suffix(".tmp")
    with tmp.open("w") as fh:
        for t, values in _samples:
            fh.write(json.dumps({"t": t, "v": values}) + "\n")
    tmp.replace(HISTORY_FILE)


def append(limits):
    """Record one poll. limits is the web-facing list of limit dicts."""
    values = {
        item["label"]: item["percent"]
        for item in limits
        if item.get("percent") is not None
    }
    if not values:
        return
    now = time.time()
    with _lock:
        _load()
        _samples.append((now, values))

        cutoff = now - HISTORY_DAYS * 86400
        expired = 0
        while _samples and _samples[0][0] < cutoff:
            _samples.pop(0)
            expired += 1

        try:
            if expired > 720:  # a day's worth of dead rows: rewrite
                _compact()
            else:
                with HISTORY_FILE.open("a") as fh:
                    fh.write(json.dumps({"t": now, "v": values}) + "\n")
        except OSError as exc:
            print("history write failed: %s" % exc, flush=True)


def query(hours, max_points=600):
    """Samples for the last `hours`, bucket-max downsampled per series.

    Max (not mean) per bucket: these are limit percentages, so the peaks
    are the part you care about.

    -> {"series": {label: [[t, pct], ...]}, "from": t0, "to": t1}
    """
    now = time.time()
    start = now - hours * 3600
    with _lock:
        _load()
        window = [s for s in _samples if s[0] >= start]

    labels = []
    for _, values in window:
        for label in values:
            if label not in labels:
                labels.append(label)

    bucket = max(1.0, (hours * 3600) / max_points)
    series = {}
    for label in labels:
        points, current_bucket, best = [], None, None
        for t, values in window:
            if label not in values:
                continue
            b = int((t - start) / bucket)
            if b != current_bucket:
                if best is not None:
                    points.append(best)
                current_bucket, best = b, None
            if best is None or values[label] >= best[1]:
                best = [round(t), values[label]]
        if best is not None:
            points.append(best)
        series[label] = points

    return {"series": series, "from": round(start), "to": round(now)}
