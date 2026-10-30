"""MeshCore OTA wire parser, Ed25519 signature verifier, and heuristic ghost-hop estimator."""

from __future__ import annotations

import hashlib
import itertools
import math
import struct
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROUTE_TYPES = {0: "TRANSPORT_FLOOD", 1: "FLOOD", 2: "DIRECT", 3: "TRANSPORT_DIRECT"}
PAYLOAD_TYPES = {
    0: "REQ",
    1: "RESPONSE",
    2: "TXT_MSG",
    3: "ACK",
    4: "ADVERT",
    5: "GRP_TXT",
    6: "GRP_DATA",
    7: "ANON_REQ",
    8: "PATH",
    9: "TRACE",
}
ADV_ROLES = {1: "COMPANION", 2: "REPEATER", 3: "ROOM_SERVER", 4: "SENSOR"}


@dataclass
class DecodedPacket:
    packet_hash: str
    route: str
    payload_type: str
    hop_size: int
    hops: list[str]
    payload: bytes
    snr: float | None = None
    rssi: float | None = None
    advert: dict[str, Any] | None = None
    ts: int = field(default_factory=lambda: int(time.time()))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_meshcore_packet(
    raw: bytes, snr: float | None = None, rssi: float | None = None
) -> DecodedPacket | None:
    """Parse a raw MeshCore LoRa frame and verify Ed25519 signature if it is an ADVERT."""
    if len(raw) < 2:
        return None
    header = raw[0]
    route_code = header & 0x03
    ptype_code = (header >> 2) & 0x0F
    version = (header >> 6) & 0x03
    if version != 0:
        return None

    idx = 1
    if route_code in (0, 3):  # transport codes present (4 bytes)
        if len(raw) < 6:
            return None
        idx += 4

    if len(raw) <= idx:
        return None
    path_byte = raw[idx]
    idx += 1
    hop_count = path_byte & 0x3F
    hop_size = ((path_byte >> 6) & 0x03) + 1
    if hop_size > 3 or hop_count > 64:
        return None
    path_bytes_len = hop_count * hop_size
    if len(raw) < idx + path_bytes_len:
        return None

    hops: list[str] = []
    for i in range(hop_count):
        start = idx + i * hop_size
        hops.append(raw[start : start + hop_size].hex().upper())
    payload = raw[idx + path_bytes_len :]

    phash = hashlib.sha256(bytes([header]) + payload).hexdigest()[:16].upper()
    route = ROUTE_TYPES.get(route_code, "FLOOD")
    ptype = PAYLOAD_TYPES.get(ptype_code, f"TYPE_{ptype_code}")

    advert = _decode_advert(payload) if ptype == "ADVERT" else None
    return DecodedPacket(
        packet_hash=phash,
        route=route,
        payload_type=ptype,
        hop_size=hop_size,
        hops=hops,
        payload=payload,
        snr=snr,
        rssi=rssi,
        advert=advert,
    )


def _decode_advert(payload: bytes) -> dict[str, Any] | None:
    """Decode and Ed25519-verify a MeshCore ADVERT payload: pubkey(32) + ts(4) + sig(64) + appdata."""
    if len(payload) < 101:
        return None
    pubkey = payload[:32]
    advert_ts = struct.unpack("<I", payload[32:36])[0]
    signature = payload[36:100]
    appdata = payload[100:]

    signed_msg = pubkey + payload[32:36] + appdata
    verified = False
    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, signed_msg)
        verified = True
    except InvalidSignature:
        verified = False

    flags = appdata[0]
    role = ADV_ROLES.get(flags & 0x0F, "COMPANION")
    pos = 1
    lat: float | None = None
    lon: float | None = None
    if flags & 0x10:  # has location
        if len(appdata) < pos + 8:
            return None
        lat_i, lon_i = struct.unpack("<ii", appdata[pos : pos + 8])
        lat = round(lat_i / 1e6, 6)
        lon = round(lon_i / 1e6, 6)
        pos += 8
    if flags & 0x20:  # feat1 (2B)
        pos += 2
    if flags & 0x40:  # feat2 (2B)
        pos += 2
    name: str | None = None
    if (flags & 0x80) and pos < len(appdata):
        name = appdata[pos:].decode("utf-8", errors="replace").strip("\x00").strip()

    return {
        "pubkey": pubkey.hex(),
        "id": f"mc:{pubkey.hex()}",
        "advert_ts": advert_ts,
        "verified": verified,
        "role": role,
        "lat": lat,
        "lon": lon,
        "name": name,
    }


def build_flight_from_packet(
    pkt: DecodedPacket,
    observer_id: str,
    observer_name: str,
    observer_lat: float,
    observer_lon: float,
    known_nodes: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Construct a Sixi Map `PacketFlight` with heuristic ghost-hop estimation."""
    waypoints: list[dict[str, Any]] = []
    if pkt.advert and pkt.advert.get("lat") is not None and pkt.advert.get("lon") is not None:
        adv = pkt.advert
        waypoints.append(
            {
                "id": adv["id"],
                "name": adv.get("name") or adv["id"][:10],
                "hash": adv["pubkey"][:4].upper(),
                "lat": float(adv["lat"]),
                "lon": float(adv["lon"]),
                "role": adv.get("role") or "REPEATER",
                "ghost": False,
            }
        )

    anchor_lat = waypoints[0]["lat"] if waypoints else observer_lat
    anchor_lon = waypoints[0]["lon"] if waypoints else observer_lon

    for idx, h in enumerate(pkt.hops):
        matched = next(
            (
                n
                for nid, n in known_nodes.items()
                if nid.startswith(f"mc:{h.lower()}")
                and n.get("lat") is not None
                and n.get("lon") is not None
            ),
            None,
        )
        if matched is not None:
            waypoints.append(
                {
                    "id": matched["id"],
                    "name": matched.get("name") or matched["id"][:10],
                    "hash": h,
                    "lat": float(matched["lat"]),
                    "lon": float(matched["lon"]),
                    "role": matched.get("role") or "REPEATER",
                    "ghost": False,
                }
            )
            anchor_lat, anchor_lon = float(matched["lat"]), float(matched["lon"])
        else:
            # Heuristic ghost-hop position estimate
            seed = int(h[:4].ljust(4, "0"), 16)
            angle = math.radians(seed % 360)
            snr_v = pkt.snr if pkt.snr is not None else -6.0
            est_km = max(4.0, min(48.0, 16.0 * (10.0 ** (-snr_v / 26.0))))
            if waypoints:
                w = (idx + 1) / (len(pkt.hops) + 1)
                glat = round(anchor_lat + (observer_lat - anchor_lat) * w + 0.015 * math.sin(angle), 5)
                glon = round(anchor_lon + (observer_lon - anchor_lon) * w + 0.015 * math.cos(angle), 5)
            else:
                glat = round(observer_lat + (est_km / 111.195) * math.sin(angle), 5)
                glon = round(
                    observer_lon
                    + (est_km / (111.195 * max(0.2, math.cos(math.radians(observer_lat)))))
                    * math.cos(angle),
                    5,
                )
            waypoints.append(
                {
                    "id": f"ghost:{h.lower()}",
                    "name": f"👻 ~{h}",
                    "hash": h,
                    "lat": glat,
                    "lon": glon,
                    "role": "GHOST_REPEATER",
                    "ghost": True,
                    "uncertainty_km": round(max(4.0, est_km * 0.28), 1),
                }
            )
            anchor_lat, anchor_lon = glat, glon

    waypoints.append(
        {
            "id": observer_id,
            "name": observer_name,
            "hash": "OBS",
            "lat": observer_lat,
            "lon": observer_lon,
            "role": "COMPANION",
            "ghost": False,
        }
    )

    if len(waypoints) < 2:
        return None

    total_km = 0.0
    max_hop_km = 0.0
    for a, b in itertools.pairwise(waypoints):
        d = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        total_km += d
        max_hop_km = max(max_hop_km, d)

    has_ghost = any(w.get("ghost") for w in waypoints)
    hop_count = len(waypoints) - 1
    anomaly_tag = (
        "DX_RECORD"
        if max_hop_km >= 80.0
        else "GHOST_TRIANGULATED"
        if has_ghost
        else "SUBNOISE_HERO"
        if (pkt.snr is not None and pkt.snr <= -7.0)
        else "DEEP_CASCADE"
        if hop_count >= 4
        else None
    )
    anomaly_label = (
        f"🏆 DX Hop {round(max_hop_km)} km"
        if anomaly_tag == "DX_RECORD"
        else f"👻 Ghost ~{pkt.hops[0] if pkt.hops else '?'}"
        if anomaly_tag == "GHOST_TRIANGULATED"
        else f"🛡️ Sub-Noise SX1262 ({pkt.snr:+.1f} dB)"
        if anomaly_tag == "SUBNOISE_HERO" and pkt.snr is not None
        else f"🌊 {hop_count}-Hop Cascade"
        if anomaly_tag == "DEEP_CASCADE"
        else None
    )

    return {
        "id": f"fl:{pkt.ts}:{pkt.packet_hash[:8]}",
        "proto": "meshcore",
        "pkt_type": pkt.payload_type,
        "ts": pkt.ts,
        "from_id": waypoints[0]["id"],
        "from_name": waypoints[0]["name"],
        "to_id": observer_id,
        "to_name": observer_name,
        "snr": pkt.snr,
        "total_km": round(total_km, 1),
        "max_hop_km": round(max_hop_km, 1),
        "hop_count": hop_count,
        "waypoints": waypoints,
        "predicted_next": [],
        "anomaly_tag": anomaly_tag,
        "anomaly_label": anomaly_label,
    }
