"""Short traffic history for the UniFi gateway ports, kept in memory for the map's bandwidth graphs.

Two tiers per port: every reading (about every 15 s) for the last 2 hours, and 1-minute averages for the
last 24 hours. Nothing is written to disk: the history starts again when Home Assistant restarts.
Pure Python (no Home Assistant imports) so it can be tested on its own.
"""

from __future__ import annotations

import time
from collections import deque

FINE_SECONDS = 2 * 3600
COARSE_STEP = 60
COARSE_SECONDS = 24 * 3600
RANGES = {"30m": 1800, "2h": 7200, "24h": 86400}
MAX_POINTS = 360  # per series and request; longer ranges are averaged down to this


class RateHistory:
    def __init__(self) -> None:
        self._fine: dict[str, deque] = {}
        self._coarse: dict[str, deque] = {}
        self._bucket: dict[str, list] = {}  # port -> [bucket_start, n, rx_sum, tx_sum]
        self.names: dict[str, dict] = {}  # port -> {"name", "role", "speed"}

    def add(self, port: str, rx: float | None, tx: float | None, now: float | None = None) -> None:
        if rx is None and tx is None:
            return
        now = now or time.time()
        rx, tx = float(rx or 0), float(tx or 0)
        fine = self._fine.setdefault(port, deque())
        fine.append((now, rx, tx))
        while fine and fine[0][0] < now - FINE_SECONDS:
            fine.popleft()
        start = now - now % COARSE_STEP
        b = self._bucket.get(port)
        if b and b[0] != start:
            coarse = self._coarse.setdefault(port, deque())
            coarse.append((b[0] + COARSE_STEP / 2, b[2] / b[1], b[3] / b[1]))
            while coarse and coarse[0][0] < now - COARSE_SECONDS:
                coarse.popleft()
            b = None
        if not b:
            b = self._bucket[port] = [start, 0, 0.0, 0.0]
        b[1] += 1
        b[2] += rx
        b[3] += tx

    def series(self, rng: str = "2h", now: float | None = None) -> dict:
        """{"range", "from", "to", "series": {port: [[ts, rx, tx], ...]}} with at most MAX_POINTS per port."""
        now = now or time.time()
        span = RANGES.get(rng, RANGES["2h"])
        since = now - span
        out: dict[str, list] = {}
        for port in set(self._fine) | set(self._coarse):
            if span <= FINE_SECONDS:
                pts = [p for p in self._fine.get(port, ()) if p[0] >= since]
            else:
                older = [p for p in self._coarse.get(port, ()) if p[0] >= since]
                first_fine = older[-1][0] if older else since
                pts = older + [p for p in self._fine.get(port, ()) if p[0] > first_fine + COARSE_STEP / 2]
            out[port] = _downsample(pts, MAX_POINTS)
        return {"range": rng, "from": since, "to": now, "series": out, "ports": self.names}


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
