"""
IEC 61162 / S-57 protocol helpers.

IEC 61162-1 is largely equivalent to NMEA 0183 at the sentence level.
IEC 61162-450 wraps those sentences in UDP multicast datagrams (PGN-based).
We detect and decode both.
"""
import re
import struct
from dataclasses import dataclass
from typing import Optional

from .nmea import parse_sentence, NMEASentence


# IEC 61162-450 multicast group used by many bridge systems
IEC_MULTICAST_GROUP = "239.192.0.1"
IEC_MULTICAST_PORT = 60001


@dataclass
class IECFrame:
    """Wrapper around an IEC 61162-450 UDP datagram."""
    source_id: str          # SourceName field
    pgn: Optional[int]      # PGN if present
    nmea_sentences: list[NMEASentence]
    raw: bytes


def parse_iec_udp_frame(data: bytes) -> Optional[IECFrame]:
    """
    Attempt to parse an IEC 61162-450 UDP datagram.
    These contain ASCII NMEA sentences, optionally prefixed with metadata.
    """
    try:
        text = data.decode("ascii", errors="ignore").strip()
    except Exception:
        return None

    # Extract any embedded NMEA sentences
    sentences = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("$"):
            s = parse_sentence(line)
            if s:
                sentences.append(s)

    if not sentences:
        return None

    # Try to extract a PGN from the first 4 bytes (big-endian uint32) if present
    pgn = None
    source_id = "unknown"
    if len(data) >= 4:
        try:
            possible_pgn = struct.unpack(">I", data[:4])[0]
            if 0 < possible_pgn < 0x3FFFF:
                pgn = possible_pgn
        except Exception:
            pass

    return IECFrame(source_id=source_id, pgn=pgn, nmea_sentences=sentences, raw=data)


def is_iec_61162_data(data: bytes) -> bool:
    """
    Heuristic: IEC 61162 data either contains NMEA-formatted sentences
    or matches the UDP multicast frame structure.
    """
    try:
        text = data.decode("ascii", errors="ignore")
        return bool(re.search(r"\$[A-Z]{5},", text))
    except Exception:
        return False
