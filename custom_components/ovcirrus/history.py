"""Traffic history for the UniFi gateway ports, behind the map's bandwidth graphs.

Two tiers per port: every reading (one per sample interval) for the fine window, and 1-minute averages
for the long window. Both windows are set in the integration options. The history can be written to disk
(`to_dict` / `load`) so it survives a Home Assistant restart.
Pure Python (no Home Assistant imports) so it can be tested on its own.
"""

from __future__ import annotations

import time
from collections import deque

COARSE_STEP = 60
DEFAULT_FINE_SECONDS = 2 * 3600
DEFAULT_LONG_SECONDS = 24 * 3600
# Chart ranges the card can ask for, in seconds.
RANGES = {
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "24h": 24 * 3600,
    "3d": 3 * 86400,
    "7d": 7 * 86400,
}
MAX_POINTS = 360  # per series and request; longer ranges are averaged down to this
SAVE_VERSION = 1


class RateHistory:
    def __init__(self, fine_seconds: int = DEFAULT_FINE_SECONDS, long_seconds: int = DEFAULT_LONG_SECONDS) -> None:
        self.fine_seconds = int(fine_seconds)
        self.long_seconds = max(int(long_seconds), self.fine_seconds)
        self._fine: dict[str, deque] = {}
        self._coarse: dict[str, deque] = {}
        self._bucket: dict[str, list] = {}  # port -> [bucket_start, n, rx_sum, tx_sum]
        self.names: dict[str, dict] = {}  # port -> {"name", "role", "speed", "up"}

    def add(self, port: str, rx: float | None, tx: float | None, now: float | None = None) -> None:
        if rx is None and tx is None:
            return
        now = now or time.time()
        rx, tx = float(rx or 0), float(tx or 0)
        fine = self._fine.setdefault(port, deque())
        fine.append((now, rx, tx))
        while fine and fine[0][0] < now - self.fine_seconds:
            fine.popleft()
        start = now - now % COARSE_STEP
        b = self._bucket.get(port)
        if b and b[0] != start:
            coarse = self._coarse.setdefault(port, deque())
            coarse.append((b[0] + COARSE_STEP / 2, b[2] / b[1], b[3] / b[1]))
            while coarse and coarse[0][0] < now - self.long_seconds:
                coarse.popleft()
            b = None
        if not b:
            b = self._bucket[port] = [start, 0, 0.0, 0.0]
        b[1] += 1
        b[2] += rx
        b[3] += tx

    def ranges(self) -> list[str]:
        """The chart ranges this history can fill."""
        return [k for k, s in RANGES.items() if s <= self.long_seconds]

    def series(self, rng: str = "2h", now: float | None = None) -> dict:
        """{"range", "from", "to", "series": {port: [[ts, rx, tx], ...]}} with at most MAX_POINTS per port."""
        now = now or time.time()
        span = min(RANGES.get(rng, RANGES["2h"]), self.long_seconds)
        since = now - span
        out: dict[str, list] = {}
        for port in set(self._fine) | set(self._coarse):
            fine = self._fine.get(port, ())
            if span <= self.fine_seconds:
                pts = [p for p in fine if p[0] >= since]
            else:
                # 1-minute averages up to where the fine readings start, then the fine readings.
                fine_from = fine[0][0] if fine else now
                older = [p for p in self._coarse.get(port, ()) if since <= p[0] < fine_from]
                pts = older + [p for p in fine if p[0] >= since]
            out[port] = _downsample(pts, MAX_POINTS)
        return {"range": rng, "from": since, "to": now, "series": out, "ports": self.names}

    # ---- saving to disk --------------------------------------------------------
    def to_dict(self) -> dict:
        def pack(pts) -> list:
            return [[round(t), round(rx, 1), round(tx, 1)] for t, rx, tx in pts]

        return {
            "version": SAVE_VERSION,
            "fine": {k: pack(v) for k, v in self._fine.items()},
            "coarse": {k: pack(v) for k, v in self._coarse.items()},
            "names": self.names,
        }

    def load(self, data: dict | None, now: float | None = None) -> None:
        """Take back a saved history, dropping anything older than the current windows."""
        if not isinstance(data, dict) or data.get("version") != SAVE_VERSION:
            return
        now = now or time.time()

        def unpack(rows, keep: int) -> deque:
            return deque((float(t), float(rx), float(tx)) for t, rx, tx in rows or () if now - keep <= t <= now)

        for port, rows in (data.get("fine") or {}).items():
            pts = unpack(rows, self.fine_seconds)
            if pts:
                self._fine[port] = pts
        for port, rows in (data.get("coarse") or {}).items():
            pts = unpack(rows, self.long_seconds)
            if pts:
                self._coarse[port] = pts
        # Readings that are now too old for the fine window but inside the long one become 1-minute averages,
        # so shortening the fine window keeps that traffic in the long history.
        for port, rows in (data.get("fine") or {}).items():
            old = [p for p in unpack(rows, self.long_seconds) if p[0] < now - self.fine_seconds]
            if not old:
                continue
            coarse = self._coarse.setdefault(port, deque())
            have = {int(p[0] // COARSE_STEP) for p in coarse}
            buckets: dict[int, list] = {}
            for t, rx, tx in old:
                b = buckets.setdefault(int(t // COARSE_STEP), [0, 0.0, 0.0])
                b[0] += 1
                b[1] += rx
                b[2] += tx
            for k, (n, rx, tx) in buckets.items():
                if k not in have:
                    coarse.append((k * COARSE_STEP + COARSE_STEP / 2, rx / n, tx / n))
            self._coarse[port] = deque(sorted(coarse))
        self.names.update(data.get("names") or {})


def _downsample(pts: list, limit: int) -> list:
    if len(pts) <= limit:
        return [[round(t, 1), round(rx, 1), round(tx, 1)] for t, rx, tx in pts]
    size = len(pts) / limit
    res = []
    for i in range(limit):
        chunk = pts[int(i * size): int((i + 1) * size)] or pts[int(i * size): int(i * size) + 1]
        n = len(chunk)
        res.append([round(sum(p[0] for p in chunk) / n, 1), round(sum(p[1] for p in chunk) / n, 1),
                    round(sum(p[2] for p in chunk) / n, 1)])
    return res
