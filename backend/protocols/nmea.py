"""
NMEA 0183 parser — handles common sentences from ship navigation systems.
Reference: https://www.nmea.org/Assets/NMEA%200183%20Standards.pdf
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class NMEASentence:
    raw: str
    talker: str        # e.g. "GP", "HC", "HE", "II"
    sentence_type: str # e.g. "GGA", "RMC", "HDT"
    fields: list[str]
    checksum_ok: bool
    decoded: dict


def _verify_checksum(sentence: str) -> bool:
    """XOR checksum verification."""
    try:
        if "*" not in sentence:
            return False
        body, cs = sentence.strip().lstrip("$").split("*")
        expected = int(cs, 16)
        actual = 0
        for c in body:
            actual ^= ord(c)
        return actual == expected
    except Exception:
        return False


def _decode_gga(fields: list[str]) -> dict:
    """Global Positioning System Fix Data."""
    try:
        lat_raw = fields[1]
        lat_dir = fields[2]
        lon_raw = fields[3]
        lon_dir = fields[4]
        fix_quality = int(fields[5]) if fields[5] else None
        num_sats = int(fields[6]) if fields[6] else None
        altitude = float(fields[8]) if fields[8] else None

        def dm_to_dd(dm, direction):
            if not dm:
                return None
            dot = dm.index(".")
            deg = float(dm[:dot - 2])
            minutes = float(dm[dot - 2:])
            dd = deg + minutes / 60
            if direction in ("S", "W"):
                dd = -dd
            return round(dd, 6)

        return {
            "type": "GPS Fix",
            "latitude": dm_to_dd(lat_raw, lat_dir),
            "longitude": dm_to_dd(lon_raw, lon_dir),
            "fix_quality": fix_quality,
            "satellites": num_sats,
            "altitude_m": altitude,
        }
    except Exception as e:
        return {"type": "GPS Fix", "error": str(e)}


def _decode_rmc(fields: list[str]) -> dict:
    """Recommended Minimum Navigation Information."""
    try:
        status = "Active" if fields[2] == "A" else "Void"
        speed_knots = float(fields[6]) if fields[6] else None
        course = float(fields[7]) if fields[7] else None
        return {
            "type": "Navigation (RMC)",
            "status": status,
            "speed_knots": speed_knots,
            "course_deg": course,
        }
    except Exception as e:
        return {"type": "Navigation (RMC)", "error": str(e)}


def _decode_hdt(fields: list[str]) -> dict:
    """Heading True."""
    try:
        heading = float(fields[1]) if fields[1] else None
        return {"type": "True Heading", "heading_deg": heading}
    except Exception as e:
        return {"type": "True Heading", "error": str(e)}


def _decode_dbt(fields: list[str]) -> dict:
    """Depth Below Transducer."""
    try:
        depth_m = float(fields[3]) if fields[3] else None
        return {"type": "Depth", "depth_m": depth_m}
    except Exception as e:
        return {"type": "Depth", "error": str(e)}


def _decode_vhw(fields: list[str]) -> dict:
    """Water Speed and Heading."""
    try:
        heading_true = float(fields[1]) if fields[1] else None
        speed_knots = float(fields[5]) if fields[5] else None
        return {"type": "Water Speed/Heading", "heading_deg": heading_true, "speed_knots": speed_knots}
    except Exception as e:
        return {"type": "Water Speed/Heading", "error": str(e)}


def _decode_vtg(fields: list[str]) -> dict:
    """Track Made Good and Ground Speed."""
    try:
        track_true = float(fields[1]) if fields[1] else None
        track_mag = float(fields[3]) if fields[3] else None
        speed_knots = float(fields[5]) if fields[5] else None
        speed_kmh = float(fields[7]) if fields[7] else None
        return {
            "type": "Track/Ground Speed",
            "track_true_deg": track_true,
            "track_magnetic_deg": track_mag,
            "speed_knots": speed_knots,
            "speed_kmh": speed_kmh,
        }
    except Exception as e:
        return {"type": "Track/Ground Speed", "error": str(e)}


def _decode_gll(fields: list[str]) -> dict:
    """Geographic Position — Latitude/Longitude."""
    try:
        def dm_to_dd(dm, direction):
            if not dm:
                return None
            dot = dm.index(".")
            deg = float(dm[:dot - 2])
            minutes = float(dm[dot - 2:])
            dd = deg + minutes / 60
            if direction in ("S", "W"):
                dd = -dd
            return round(dd, 6)

        lat = dm_to_dd(fields[1], fields[2]) if len(fields) > 2 else None
        lon = dm_to_dd(fields[3], fields[4]) if len(fields) > 4 else None
        status = "Active" if len(fields) > 6 and fields[6] == "A" else "Void"
        return {"type": "Geographic Position", "latitude": lat, "longitude": lon, "status": status}
    except Exception as e:
        return {"type": "Geographic Position", "error": str(e)}


def _decode_zda(fields: list[str]) -> dict:
    """Time and Date."""
    try:
        utc = fields[1] if fields[1] else None
        day = int(fields[2]) if fields[2] else None
        month = int(fields[3]) if fields[3] else None
        year = int(fields[4]) if fields[4] else None
        time_str = f"{utc[:2]}:{utc[2:4]}:{utc[4:]}" if utc and len(utc) >= 6 else utc
        return {"type": "UTC Time/Date", "utc": time_str, "day": day, "month": month, "year": year}
    except Exception as e:
        return {"type": "UTC Time/Date", "error": str(e)}


def _decode_mwv(fields: list[str]) -> dict:
    """Wind Speed and Angle."""
    try:
        angle = float(fields[1]) if fields[1] else None
        reference = "True" if fields[2] == "T" else "Relative"
        speed = float(fields[3]) if fields[3] else None
        units = {"K": "km/h", "M": "m/s", "N": "knots"}.get(fields[4], fields[4]) if len(fields) > 4 else "?"
        return {
            "type": "Wind",
            "wind_angle_deg": angle,
            "reference": reference,
            "wind_speed": speed,
            "speed_units": units,
        }
    except Exception as e:
        return {"type": "Wind", "error": str(e)}


def _decode_mtw(fields: list[str]) -> dict:
    """Mean Temperature of Water."""
    try:
        temp = float(fields[1]) if fields[1] else None
        return {"type": "Water Temperature", "temperature_c": temp}
    except Exception as e:
        return {"type": "Water Temperature", "error": str(e)}


def _decode_rot(fields: list[str]) -> dict:
    """Rate of Turn."""
    try:
        rot = float(fields[1]) if fields[1] else None
        status = "Valid" if len(fields) > 2 and fields[2] == "A" else "Invalid"
        return {"type": "Rate of Turn", "rate_deg_per_min": rot, "status": status}
    except Exception as e:
        return {"type": "Rate of Turn", "error": str(e)}


_DECODERS = {
    "GGA": _decode_gga,
    "GLL": _decode_gll,
    "RMC": _decode_rmc,
    "VTG": _decode_vtg,
    "HDT": _decode_hdt,
    "DBT": _decode_dbt,
    "VHW": _decode_vhw,
    "ZDA": _decode_zda,
    "MWV": _decode_mwv,
    "MTW": _decode_mtw,
    "ROT": _decode_rot,
}


def parse_sentence(raw: str) -> Optional[NMEASentence]:
    """Parse a single NMEA 0183 sentence string."""
    raw = raw.strip()
    if not raw.startswith("$"):
        return None
    checksum_ok = _verify_checksum(raw)
    body = raw.lstrip("$").split("*")[0]
    parts = body.split(",")
    if not parts:
        return None
    header = parts[0]
    if len(header) < 5:
        return None
    talker = header[:2]
    sentence_type = header[2:]
    fields = parts
    decoder = _DECODERS.get(sentence_type, lambda f: {"type": sentence_type, "raw_fields": f[1:]})
    decoded = decoder(fields)
    return NMEASentence(
        raw=raw, talker=talker, sentence_type=sentence_type,
        fields=fields, checksum_ok=checksum_ok, decoded=decoded
    )


def is_nmea_data(data: bytes) -> bool:
    """Quick heuristic — does this look like NMEA 0183?"""
    try:
        text = data.decode("ascii", errors="ignore")
        return bool(re.search(r"\$[A-Z]{5},", text))
    except Exception:
        return False
