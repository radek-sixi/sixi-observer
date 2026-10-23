# 🛰️ sixi-observer — Open-Source LoRa & RTL-SDR Bridge for Sixi AI Map

**`sixi-observer`** is an open-source, lightweight bridge that connects your own **MeshCore / LoRa radio** (**LilyGO T-Beam Supreme SX1262**, **Heltec V3/V4**, **RAK4631**) or **RTL-SDR Blog V4** to the public [**Sixi AI Map (`map.sixi.ai`)**](https://map.sixi.ai).

By running `sixi-observer` (or using the built-in **Zero-Install WebSerial USB** mode directly in your browser), you unlock the exact same real-time SIGINT & ML capabilities on the public map that run on private Sixi Edge nodes:

- 🟢 **Ed25519 Cryptographic Verification (`Verified locally`)** — green ring around nodes whose signed advertisements were verified directly by your radio.
- ☄️ **Live Animated Packet Flights** — real-time WebGL comet trails (`Origin → Hop 1 → … → Your Radio`) with per-hop shockwave ripples and exact sub-noise SNR/RSSI.
- 👻 **ML Bayesian Ghost-Hop Triangulation** — estimates coordinates and uncertainty radius (`±km`) for unidentified repeaters (`~HASH`) heard in multi-hop flood paths.
- 🔒 **Privacy-First User Overlay** — by default (mode *private*), your receiver's location, SNR, and packets are stored in your isolated `/v1/users/<uid>/live` overlay and are visible **only to you** when signed into [`map.sixi.ai`](https://map.sixi.ai).

---

## ⚡ Quick Start (60 Seconds)

### Option 1: One-Command Run with `uvx` (No manual install needed)

1. Open [**map.sixi.ai**](https://map.sixi.ai) and click **`🛰️ Connect Radio`** in the top-right bar.
2. Sign in (GitHub / Google / Email) to copy your personal `sobs_live_...` token.
3. Plug in your **T-Beam Supreme / Heltec** via USB and run:

```bash
uvx --from git+https://github.com/radek-sixi/sixi-observer sixi-observer run \
  --serial /dev/ttyACM0 \
  --token sobs_live_YOUR_TOKEN \
  --name "My T-Beam Supreme" \
  --lat 47.37 --lon 8.54
```

> **No hardware plugged in yet?** Run with `--demo` to test live multi-hop comet animations and ML Ghost-Hop triangulation immediately:
> ```bash
> uvx --from git+https://github.com/radek-sixi/sixi-observer sixi-observer run --demo
> ```

### Option 2: Docker (24/7 Raspberry Pi / Linux / Mini-PC)

```bash
docker run -d --name sixi-observer --restart unless-stopped \
  --device /dev/ttyACM0 \
  -p 127.0.0.1:8765:8765 \
  ghcr.io/radek-sixi/sixi-observer:latest \
  run --serial /dev/ttyACM0 --token sobs_live_YOUR_TOKEN --lat 47.37 --lon 8.54
```

### Option 3: Local Zero-Latency WebSocket Bridge (`ws://127.0.0.1:8765/ws`)

`sixi-observer` also exposes a local WebSocket server on `ws://127.0.0.1:8765/ws`. In the **`🛰️ Connect Radio`** modal on `map.sixi.ai`, click **`🔗 Połącz z 127.0.0.1:8765`** to stream packets directly from your USB radio to the browser with **0 ms cloud latency** — even without an account!

---

## 🛡️ Security & Privacy Guarantees

1. **No Private Messages Ever Leave Your Device:** Encrypted direct messages (`TXT_MSG` / ECDH) and private channel keys are never decoded or transmitted.
2. **Local Ed25519 Signature Checking:** `sixi-observer` verifies the 64-byte Ed25519 signature of every MeshCore `ADVERT` frame before marking `verified_local = true`.
3. **Two Privacy Modes**, chosen in the map's **`🛰️ Connect Radio`** dialog (stored with your account; the `--mode` flag is informational only):
   - `private` *(default)*: Only your authenticated account can see your station and its received paths.
   - `community`: Keeps your station private while contributing anonymized multi-hop repeater-to-repeater flights (`REPEATER → REPEATER`) to the public map animations.

---

## 🧪 Development & Tests

```bash
git clone https://github.com/radek-sixi/sixi-observer.git
cd sixi-observer
uv run --with pytest --with ruff ruff check src tests
uv run --with pytest pytest -q
```

## 📄 License

MIT License — see [LICENSE](LICENSE).
