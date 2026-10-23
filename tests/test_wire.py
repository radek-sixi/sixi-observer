import struct

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sixi_observer.wire import build_flight_from_packet, parse_meshcore_packet


def test_parse_and_verify_ed25519_advert_and_build_flight():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw()
    ts_bytes = struct.pack("<I", 1791962000)
    # flags: role=2 (REPEATER) | 0x10 (location) | 0x80 (name)
    appdata = bytes([0x92]) + struct.pack("<ii", int(46.8902 * 1e6), int(7.5238 * 1e6)) + b"Test Relay"
    sig = priv.sign(pub + ts_bytes + appdata)
    payload = pub + ts_bytes + sig + appdata

    # header: FLOOD (1) | ADVERT (4 << 2 = 16) = 17; path_len: hop_size=2 ((1<<6) | 2 hops) = 0x42
    raw = bytes([17, 0x42]) + bytes.fromhex("B3DECF3C") + payload
    pkt = parse_meshcore_packet(raw, snr=-9.5, rssi=-114.0)
    assert pkt is not None
    assert pkt.route == "FLOOD" and pkt.payload_type == "ADVERT"
    assert pkt.hops == ["B3DE", "CF3C"]
    assert pkt.advert is not None and pkt.advert["verified"] is True
    assert pkt.advert["name"] == "Test Relay"
    assert (pkt.advert["lat"], pkt.advert["lon"]) == (46.8902, 7.5238)

    fl = build_flight_from_packet(
        pkt,
        observer_id="mc:obs:my-tbeam",
        observer_name="My T-Beam Supreme",
        observer_lat=46.95,
        observer_lon=7.45,
        known_nodes={},
    )
    assert fl is not None
    assert fl["hop_count"] == 3
    assert fl["anomaly_tag"] == "GHOST_TRIANGULATED"
    assert any(w.get("ghost") for w in fl["waypoints"])
