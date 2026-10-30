"""CLI & local/cloud bridge for `sixi-observer`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import urllib.request
from typing import Any

import websockets

from sixi_observer import __version__
from sixi_observer.wire import DecodedPacket, build_flight_from_packet, parse_meshcore_packet

log = logging.getLogger("sixi-observer")

DEFAULT_CLOUD_URL = "https://map.sixi.ai/api/v1/observer/ingest"
# --demo without --lat/--lon: a synthetic point (Bern), unrelated to any real station
DEMO_LAT, DEMO_LON = 46.95, 7.45
# the station position leaves this device only coarsened (~1 km), as the map stores it
POSITION_DECIMALS = 2


class ObserverBridge:
    def __init__(
        self,
        *,
        name: str,
        device: str,
        lat: float,
        lon: float,
        token: str | None,
        cloud_url: str,
        mode: str,
        ws_port: int,
    ) -> None:
        self.observer_id = "mc:obs:sixi-observer-local"
        self.name = name
        self.device = device
        self.lat = round(lat, POSITION_DECIMALS)
        self.lon = round(lon, POSITION_DECIMALS)
        self.token = token
        self.cloud_url = cloud_url
        self.mode = mode
        self.ws_port = ws_port
        self.known_nodes: dict[str, dict[str, Any]] = {}
        self.ws_clients: set[Any] = set()

    def observer_node(self, now: int, snr: float | None = None) -> dict[str, Any]:
        return {
            "id": self.observer_id,
            "proto": "meshcore",
            "name": self.name,
            "short": "OBS",
            "role": "COMPANION",
            "lat": self.lat,
            "lon": self.lon,
            "seen": now,
            "sources": ["sixi_observer"],
            "extra": {
                "verified_local": True,
                "site_radio": True,
                "observer": True,
                "local_device": self.device,
                **({"snr": snr} if snr is not None else {}),
            },
        }

    async def handle_packet(self, pkt: DecodedPacket) -> None:
        now = pkt.ts
        nodes = [self.observer_node(now, pkt.snr)]
        if pkt.advert and pkt.advert.get("verified"):
            adv = pkt.advert
            node_rec = {
                "id": adv["id"],
                "proto": "meshcore",
                "name": adv.get("name") or adv["id"][:10],
                "role": adv.get("role") or "REPEATER",
                "lat": adv.get("lat"),
                "lon": adv.get("lon"),
                "seen": now,
                "sources": ["sixi_observer"],
                "extra": {
                    "verified_local": True,
                    "heard_by": self.observer_id,
                    "snr": pkt.snr,
                    "rssi": pkt.rssi,
                    "last_path": {
                        "to": self.observer_id,
                        "hops": [{"hash": h} for h in pkt.hops],
                        "ts": now,
                    },
                },
            }
            self.known_nodes[adv["id"]] = node_rec
            nodes.append(node_rec)

        flight = build_flight_from_packet(
            pkt, self.observer_id, self.name, self.lat, self.lon, self.known_nodes
        )
        flights = [flight] if flight else []

        # Broadcast to local browser clients on ws://127.0.0.1:8765/ws
        if self.ws_clients:
            delta_msg = json.dumps({"type": "delta", "nodes": nodes, "links": [], "removed": []})
            flight_msg = json.dumps({"type": "flights", "flights": flights}) if flights else None
            for ws in list(self.ws_clients):
                try:
                    await ws.send(delta_msg)
                    if flight_msg:
                        await ws.send(flight_msg)
                except OSError:
                    self.ws_clients.discard(ws)

        # Push to Sixi Map Cloud Overlay if a token is configured
        if self.token:
            await asyncio.to_thread(self._push_cloud, nodes, flights)

    def _push_cloud(
        self, nodes: list[dict[str, Any]], flights: list[dict[str, Any]]
    ) -> None:
        body = json.dumps(
            {
                "observer_name": self.name,
                "device": self.device,
                "mode": self.mode,
                "nodes": nodes,
                "links": [],
                "flights": flights,
            }
        ).encode()
        req = urllib.request.Request(
            self.cloud_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": f"sixi-observer/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                log.info("Cloud sync OK (%s)", resp.status)
        except OSError as e:
            log.warning("Cloud sync failed: %s", e)

    async def ws_handler(self, websocket: Any) -> None:
        self.ws_clients.add(websocket)
        now = int(time.time())
        await websocket.send(
            json.dumps(
                {
                    "type": "delta",
                    "nodes": [self.observer_node(now), *self.known_nodes.values()],
                    "links": [],
                    "removed": [],
                }
            )
        )
        try:
            async for _ in websocket:
                pass
        finally:
            self.ws_clients.discard(websocket)


async def run_serial_loop(bridge: ObserverBridge, port: str, baud: int) -> None:
    import serial

    log.info("Opening serial port %s @ %d bps...", port, baud)
    ser = serial.Serial(port, baud, timeout=0.2)
    while True:
        line_bytes = await asyncio.to_thread(ser.readline)
        if not line_bytes:
            await asyncio.sleep(0.05)
            continue
        line = line_bytes.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
                raw_hex = d.get("raw") or d.get("data")
                if isinstance(raw_hex, str):
                    pkt = parse_meshcore_packet(
                        bytes.fromhex(raw_hex),
                        snr=float(d["snr"]) if "snr" in d else None,
                        rssi=float(d["rssi"]) if "rssi" in d else None,
                    )
                    if pkt:
                        await bridge.handle_packet(pkt)
            except (ValueError, KeyError, TypeError) as e:
                log.debug("Ignored serial JSON line: %s", e)


async def run_demo_loop(bridge: ObserverBridge) -> None:
    log.info("Running in --demo mode (emitting simulated multi-hop LoRa frames every 8s)")
    while True:
        pkt = DecodedPacket(
            packet_hash=f"{int(time.time()):016X}",
            route="FLOOD",
            payload_type="ADVERT",
            hop_size=2,
            hops=["B3DE", "CF3C"],
            payload=b"",
            snr=-8.5,
            rssi=-112.0,
            advert={
                "pubkey": "0d28" + "a1" * 30,
                "id": "mc:0d28" + "a1" * 30,
                "advert_ts": int(time.time()),
                "verified": True,
                "role": "REPEATER",
                "lat": bridge.lat - 0.06,
                "lon": bridge.lon + 0.07,
                "name": "Demo Relay",
            },
        )
        await bridge.handle_packet(pkt)
        await asyncio.sleep(8.0)


async def async_main(args: argparse.Namespace) -> None:
    bridge = ObserverBridge(
        name=args.name,
        device=args.device,
        lat=args.lat,
        lon=args.lon,
        token=args.token,
        cloud_url=args.cloud_url,
        mode=args.mode,
        ws_port=args.ws_port,
    )
    async with websockets.serve(bridge.ws_handler, "127.0.0.1", args.ws_port):
        log.info("Local WebSocket bridge listening on ws://127.0.0.1:%d/ws", args.ws_port)
        if args.demo or not args.serial:
            await run_demo_loop(bridge)
        else:
            await run_serial_loop(bridge, args.serial, args.baud)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sixi-observer",
        description="Connect any MeshCore LoRa radio to Sixi AI Map (map.sixi.ai)",
    )
    sub = p.add_subparsers(dest="cmd")
    run_p = sub.add_parser("run", help="Start the observer bridge")
    run_p.add_argument("--serial", default="", help="Serial device (e.g. /dev/ttyACM0, /dev/ttyUSB0, or COM3)")
    run_p.add_argument("--baud", type=int, default=115200, help="Serial baud rate (default: 115200)")
    run_p.add_argument("--token", default="", help="Personal observer token (sobs_live_...)")
    run_p.add_argument("--name", default="MeshCore Observer", help="Station name on the map")
    run_p.add_argument("--device", default="MeshCore USB Radio", help="Hardware model label (optional)")
    run_p.add_argument(
        "--lat", type=float, help="Station latitude (required except with --demo; sent rounded to 0.01°)"
    )
    run_p.add_argument(
        "--lon", type=float, help="Station longitude (required except with --demo; sent rounded to 0.01°)"
    )
    run_p.add_argument(
        "--mode",
        choices=("private", "community"),
        default="private",
        help="Informational: the map uses the privacy mode you choose in Connect Radio (private or community)",
    )
    run_p.add_argument("--cloud-url", default=DEFAULT_CLOUD_URL, help="Cloud ingest endpoint")
    run_p.add_argument("--ws-port", type=int, default=8765, help="Local WebSocket port (default: 8765)")
    run_p.add_argument("--demo", action="store_true", help="Simulate multi-hop LoRa frames without USB radio")

    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = build_parser()
    args = p.parse_args(argv)
    if args.cmd == "run" and (args.lat is None or args.lon is None):
        if not (args.demo or not args.serial):
            p.error("run: --lat and --lon are required (your station's position, e.g. --lat 47.37 --lon 8.54)")
        args.lat, args.lon = DEMO_LAT, DEMO_LON
    return args


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if args.cmd != "run":
        build_parser().print_help()
        return
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
