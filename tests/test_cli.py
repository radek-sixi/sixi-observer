from urllib.parse import urlparse

import pytest

from sixi_observer.cli import DEFAULT_CLOUD_URL, DEMO_LAT, DEMO_LON, ObserverBridge, parse_args


def test_ingests_to_map_sixi_ai():
    u = urlparse(DEFAULT_CLOUD_URL)
    assert (u.scheme, u.netloc, u.path) == ("https", "map.sixi.ai", "/api/v1/observer/ingest")
    assert parse_args(["run", "--demo"]).cloud_url == DEFAULT_CLOUD_URL


def test_a_radio_needs_an_explicit_position():
    with pytest.raises(SystemExit):
        parse_args(["run", "--serial", "/dev/ttyACM0"])
    args = parse_args(["run", "--serial", "/dev/ttyACM0", "--lat", "47.3769", "--lon", "8.5417"])
    assert (args.lat, args.lon) == (47.3769, 8.5417)


def test_demo_uses_a_synthetic_point():
    for argv in (["run", "--demo"], ["run"]):
        args = parse_args(argv)
        assert (args.lat, args.lon) == (DEMO_LAT, DEMO_LON)


def test_position_leaves_the_device_coarsened():
    b = ObserverBridge(
        name="t", device="d", lat=47.37691, lon=8.54172, token=None,
        cloud_url=DEFAULT_CLOUD_URL, mode="private", ws_port=0,
    )
    node = b.observer_node(0)
    assert (node["lat"], node["lon"]) == (47.38, 8.54)
