"""
Anomaly Detection — monitors DataBus frames and flags statistical outliers.

Maintains sliding-window baselines per channel per metric (e.g., speed, depth,
heading) and alerts when values deviate beyond configurable sigma thresholds.
"""
import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from .mission_log import emit as log_event

log = logging.getLogger("warclaw.anomaly")

# Sliding window of values for each metric key
WINDOW_SIZE = 200
DEFAULT_SIGMA = 3.0  # Standard deviations before flagging


@dataclass
class MetricWindow:
    """Running statistics for a single numeric metric."""
    values: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    last_alert_ts: float = 0.0
    alert_cooldown_s: float = 30.0  # Suppress repeated alerts within cooldown

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float:
        if not self.values:
            return 0.0
        return sum(self.values) / len(self.values)

    @property
    def stddev(self) -> float:
        if len(self.values) < 2:
            return 0.0
        m = self.mean
        variance = sum((x - m) ** 2 for x in self.values) / (len(self.values) - 1)
        return math.sqrt(variance)

    def add(self, value: float) -> None:
        self.values.append(value)

    def is_anomalous(self, value: float, sigma: float = DEFAULT_SIGMA) -> bool:
        """Check if a value is outside mean ± sigma*stddev."""
        if self.count < 10:
            return False  # Need enough data for a baseline
        s = self.stddev
        if s == 0:
            return False  # No variance — can't detect anomalies
        z = abs(value - self.mean) / s
        return z > sigma

    def can_alert(self) -> bool:
        now = time.time()
        if now - self.last_alert_ts > self.alert_cooldown_s:
            self.last_alert_ts = now
            return True
        return False


class AnomalyDetector:
    """Tracks numeric metrics from protocol data and detects anomalies."""

    def __init__(self, sigma: float = DEFAULT_SIGMA):
        self.sigma = sigma
        # Key: "channel:metric_name" → MetricWindow
        self._metrics: dict[str, MetricWindow] = defaultdict(MetricWindow)
        self.total_anomalies: int = 0

    def check(self, channel: str, decoded: dict) -> list[dict]:
        """
        Check decoded protocol data for anomalies.
        Returns a list of anomaly dicts (may be empty).
        """
        anomalies = []

        # Extract all numeric values from decoded data
        for key, value in decoded.items():
            if not isinstance(value, (int, float)) or value is None:
                continue
            if key in ("type", "error"):
                continue

            metric_key = f"{channel}:{key}"
            window = self._metrics[metric_key]

            if window.is_anomalous(value, self.sigma) and window.can_alert():
                self.total_anomalies += 1
                anomaly = {
                    "channel": channel,
                    "metric": key,
                    "value": value,
                    "mean": round(window.mean, 4),
                    "stddev": round(window.stddev, 4),
                    "z_score": round(abs(value - window.mean) / max(window.stddev, 0.001), 2),
                    "ts": time.time(),
                }
                anomalies.append(anomaly)
                log_event("alert", "protocol",
                          f"ANOMALY: {key}={value} (baseline: {anomaly['mean']}±{anomaly['stddev']}) on {channel}",
                          anomaly)

            window.add(value)

        return anomalies

    @property
    def stats(self) -> dict:
        return {
            "total_anomalies": self.total_anomalies,
            "tracked_metrics": len(self._metrics),
            "sigma_threshold": self.sigma,
            "metrics": {
                k: {
                    "count": w.count,
                    "mean": round(w.mean, 4),
                    "stddev": round(w.stddev, 4),
                }
                for k, w in self._metrics.items()
                if w.count > 0
            },
        }


# Module-level singleton
anomaly_detector = AnomalyDetector()
