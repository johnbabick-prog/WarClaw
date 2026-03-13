"""
Data Bus — central publish/subscribe system for protocol data flowing on the ship LAN.

Producers: LAN monitor continuous ingestion, protocol parsers
Consumers: Agents, anomaly detector, dashboard, mission log

Architecture:
  - Each "channel" is a named topic (e.g., "nmea:192.168.1.10:10110")
  - Agents subscribe to channels and receive typed frames
  - The bus maintains a sliding window of recent frames per channel for baselines
"""
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

log = logging.getLogger("warclaw.data_bus")

# Maximum frames retained per channel for baseline / replay
CHANNEL_BUFFER_SIZE = 500


@dataclass
class DataFrame:
    """A single data frame flowing through the bus."""
    channel: str          # e.g. "nmea:192.168.1.10:10110"
    protocol: str         # "nmea", "modbus", "iec61162", "http", "system"
    source_ip: str
    source_port: int
    payload: dict         # decoded protocol data
    raw: str = ""         # original wire data
    ts: float = field(default_factory=time.time)


class DataBus:
    """In-process pub/sub data bus for ship protocol streams."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        # Wildcard subscribers get ALL frames
        self._global_subscribers: list[asyncio.Queue] = []
        # Per-channel recent frame buffers for baselines
        self._buffers: dict[str, deque] = {}
        # Stats
        self._total_frames = 0
        self._channel_counts: dict[str, int] = {}

    def publish(self, frame: DataFrame) -> None:
        """Publish a frame to the bus. Non-blocking."""
        self._total_frames += 1
        ch = frame.channel
        self._channel_counts[ch] = self._channel_counts.get(ch, 0) + 1

        # Buffer for baseline
        if ch not in self._buffers:
            self._buffers[ch] = deque(maxlen=CHANNEL_BUFFER_SIZE)
        self._buffers[ch].append(frame)

        # Deliver to channel subscribers
        dead = []
        for q in self._subscribers.get(ch, []):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers[ch].remove(q)

        # Deliver to global subscribers
        dead_g = []
        for q in self._global_subscribers:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                dead_g.append(q)
        for q in dead_g:
            self._global_subscribers.remove(q)

    async def subscribe(self, channel: str, max_queue: int = 256) -> AsyncIterator[DataFrame]:
        """Async generator — yields frames for a specific channel."""
        q: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(q)
        try:
            while True:
                frame = await q.get()
                yield frame
        finally:
            try:
                self._subscribers[channel].remove(q)
            except (ValueError, KeyError):
                pass

    async def subscribe_all(self, max_queue: int = 256) -> AsyncIterator[DataFrame]:
        """Async generator — yields ALL frames from ALL channels."""
        q: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._global_subscribers.append(q)
        try:
            while True:
                frame = await q.get()
                yield frame
        finally:
            try:
                self._global_subscribers.remove(q)
            except ValueError:
                pass

    def recent(self, channel: str, n: int = 50) -> list[DataFrame]:
        """Return the N most recent frames for a channel."""
        buf = self._buffers.get(channel, deque())
        return list(buf)[-n:]

    @property
    def channels(self) -> list[str]:
        return list(self._buffers.keys())

    @property
    def stats(self) -> dict:
        return {
            "total_frames": self._total_frames,
            "active_channels": len(self._buffers),
            "channels": {
                ch: {
                    "frames": self._channel_counts.get(ch, 0),
                    "buffer_size": len(buf),
                    "latest_ts": buf[-1].ts if buf else None,
                }
                for ch, buf in self._buffers.items()
            },
            "subscribers": {
                ch: len(subs) for ch, subs in self._subscribers.items() if subs
            },
            "global_subscribers": len(self._global_subscribers),
        }


# Module-level singleton
data_bus = DataBus()
