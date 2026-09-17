# Edge Agent

`edge-agent/` is the on-site process: **PCN Cloud Edge Agent**.

Continuous RTSP decode and frame capture run **here**, never inside the React/PWA browser.

## Responsibilities

- Connect to configured RTSP streams via **one long-lived FFmpeg process per camera** (TCP)
- Keep FFmpeg decoding continuously; sample frames in memory (default **2 FPS**)
- Discard warm-up and invalid/gray frames before ANPR
- Reconnect with exponential backoff + jitter when FFmpeg dies or the stream drops
- Run an ANPR processing hook on each sampled frame (no automatic ENTRY/EXIT events yet)
- Save a JPEG snapshot **only** when a vehicle/plate candidate or OCR text is found (or when debug-all is on)
- Keep a short in-memory ring buffer to pick the best frame around a detection
- Report camera health (ONLINE / OFFLINE / CONNECTING / ERROR) via heartbeat
- Queue ANPR events in SQLite (Mock ANPR path still supported)
- POST `/api/v1/edge/sync` when the cloud is reachable

## How RTSP + snapshots work

```
CCTV / NVR
  → RTSP (tcp)
    → Edge Agent (one continuous FFmpeg MJPEG pipe per camera)
      → Discard warm-up + invalid frames
      → Sample at EDGE_SAMPLE_FPS / EDGE_FRAME_INTERVAL (in memory)
      → ANPR hook (vehicle / plate / OCR) — no DB events yet
      → Save JPEG only on detection (or DEBUG_SAVE_ALL_FRAMES=true)
      → Heartbeat → Backend (status, FPS, last frame, errors)
```

**Default is not “save every frame”.** Continuous decode stays in the FFmpeg process; the agent samples a few FPS into RAM. Disk writes are detection snapshots under `EDGE_FRAME_SAVE_DIR`.

| Mode | Behavior |
| --- | --- |
| `DEBUG_SAVE_ALL_FRAMES=false` (default) | Discard normal frames; save on plate / OCR / meaningful vehicle |
| `DEBUG_SAVE_ALL_FRAMES=true` | Persist every sampled frame (legacy debug; fills disk) |
| `EDGE_ANPR_FRAME_HOOK=false` | Health/FPS only — no ANPR, no detection snapshots |

The agent polls `GET /api/v1/edge/cameras` (edge auth). Only cameras with `streaming=true` are captured. Operators start/stop capture from the Cameras page (`POST /api/v1/edge/cameras/{id}/start|stop`).

## Example RTSP URL formats

Do **not** put real passwords in docs or tickets. Prefer storing username/password as separate camera fields (encrypted at rest).

```
# Hikvision NVR (channel 1 main stream)
rtsp://192.168.1.64:554/Streaming/Channels/101

# Dahua / CP Plus style
rtsp://192.168.1.108:554/cam/realmonitor?channel=1&subtype=0

# With credentials embedded (agent receives this privately; UI never shows it)
rtsp://USERNAME:PASSWORD@192.168.1.64:554/Streaming/Channels/101
```

Subtype `0` = main stream, `1` = sub stream (often better for ANPR bandwidth).

## Configuration

See `.env.example`:

| Variable | Purpose |
| --- | --- |
| `API_BASE_URL` | Backend base URL |
| `EDGE_AGENT_ID` / `EDGE_AGENT_KEY` | Edge auth headers |
| `SITE_ID` | Site this agent serves |
| `EDGE_MOCK_MODE` | `true` = mock frames / legacy mock ANPR; `false` = real FFmpeg RTSP |
| `EDGE_MOCK_ONESHOT` | `true` = enqueue one mock plate and optionally exit |
| `EDGE_RTSP_ENABLED` | Enable continuous capture loop |
| `EDGE_FRAME_INTERVAL` | Seconds between **sampled** frames (default `0.5` = 2 FPS) |
| `EDGE_SAMPLE_FPS` | Optional; if set, overrides interval (`2` → 0.5s) |
| `EDGE_WARMUP_FRAMES` | Discard first N decoded JPEGs after FFmpeg start (default `8`) |
| `EDGE_MIN_JPEG_BYTES` | Reject undersized/incomplete JPEGs (default `1500`) |
| `EDGE_RECONNECT_MIN_SECONDS` / `EDGE_RECONNECT_MAX_SECONDS` | Backoff bounds |
| `EDGE_FRAME_SAVE_DIR` | Directory for **detection** snapshots (relative path OK) |
| `DEBUG_SAVE_ALL_FRAMES` | `false` (default) = save on detection only; `true` = dump every frame |
| `EDGE_ANPR_FRAME_HOOK` | Run ANPR on each in-memory frame (`true` default); no DB events when live off |
| `EDGE_FRAME_RING_SIZE` | In-memory frames kept for best-snapshot selection (default `15`) |
| `EDGE_SNAPSHOT_MAX_FILES` | Cap JPEGs per camera on disk (default `100`) |
| `EDGE_RTSP_TIMEOUT_SECONDS` | Wait for a valid sampled frame before reconnect |
| `ANPR_LIVE_ENABLED` | `true` = async live inference + temporal confirm → edge events |
| `ANPR_DEBUG_MODE` | Extra inference logs / plate-crop debug files |
| `ANPR_DEBUG_SAVE_FRAMES` | With debug mode: save sampled frames (~1 FPS) under `ANPR_DEBUG_FRAMES_DIR` |
| `ANPR_DEBUG_SAVE_FPS` | Debug frame dump rate (default `1`) |
| `ANPR_DEBUG_FRAMES_DIR` | Default `./data/debug-frames` |
| `ANPR_DEBUG_FRAMES_MAX_FILES` | Rotate old debug JPEGs (default `100`) |
| `ANPR_CONFIRM_MIN_OBSERVATIONS` | Consistent OCR reads required before an event (default `3`) |
| `ANPR_CONFIRM_WINDOW_SECONDS` | Confirmation window (default `2`) |
| `ANPR_MIN_OCR_CONFIDENCE` | Min OCR confidence for confirmation (shared with ANPR engine) |
| `ANPR_EVENT_COOLDOWN_SECONDS` | Per camera+plate event cooldown (default `120`) |
| `ANPR_INFER_QUEUE_SIZE` | Bounded frame queue; drops oldest when full (default `8`) |

Register:

```bash
curl -X POST http://localhost:8000/api/v1/edge/register \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Lonavala Agent\",\"site_id\":\"<site-uuid>\",\"agent_key\":\"a-long-random-key\"}"
```

Store `agent_id` and the plaintext key in the site `.env`. The API stores only a bcrypt hash.

## Mock mode (development without a camera)

```bash
cd edge-agent
pip install -r requirements.txt
# PYTHONPATH must include ../anpr-engine
set PYTHONPATH=..\anpr-engine;%PYTHONPATH%
set EDGE_MOCK_MODE=true
set EDGE_MOCK_ONESHOT=true
python -m pcn_edge.main
```

This runs the mock ANPR pipeline, enqueues one event, and attempts sync. If the API is down the row stays in SQLite (`synced = 0`).

For continuous **fake** RTSP (debug frames, health heartbeats) without a real NVR:

```bash
set EDGE_MOCK_MODE=true
set EDGE_MOCK_ONESHOT=false
set EDGE_RTSP_ENABLED=true
set EDGE_AGENT_ID=<id>
set EDGE_AGENT_KEY=<key>
python -m pcn_edge.main
```

## Live RTSP mode

Prerequisites: **FFmpeg** on PATH, camera Start'd from the UI, agent registered.

```bash
set EDGE_MOCK_MODE=false
set EDGE_RTSP_ENABLED=true
set EDGE_FRAME_INTERVAL=0.5
set EDGE_FRAME_SAVE_DIR=./data/edge-frames
set DEBUG_SAVE_ALL_FRAMES=false
set ANPR_LIVE_ENABLED=true
set ANPR_DEBUG_MODE=true
set ANPR_CONFIRM_MIN_OBSERVATIONS=3
set ANPR_CONFIRM_WINDOW_SECONDS=2
set ANPR_EVENT_COOLDOWN_SECONDS=120
python -m pcn_edge.main
```

Real CCTV → continuous FFmpeg decode → sampled frames (e.g. 2 FPS) → **bounded ANPR worker** → temporal confirmation → cooldown → edge sync → ENTRY/EXIT events + dashboard WebSocket.

With `ANPR_LIVE_ENABLED=false`, behavior stays detection-snapshots + health only (no DB events).

Older MPEG-4 cameras (e.g. Zavio F210A) often produce gray/incomplete frames on one-shot grabs; continuous TCP decode + warm-up discard avoids that.

## Security

- Never log full RTSP URLs (passwords redacted as `***`)
- Edge camera config endpoint is **edge-auth only**; PWA APIs never return `rtsp_url` or passwords
- Credentials encrypted at rest in PostgreSQL (Fernet)

## Troubleshooting connection failures

| Symptom | Likely cause |
| --- | --- |
| `RTSP URL must start with rtsp://` | Wrong scheme (http/onvif URL pasted) |
| Timeout / no frame | Firewall, wrong channel, NVR needs TCP, camera offline |
| Auth errors from FFmpeg | Wrong username/password; try separate credential fields |
| `FFmpeg is not installed` | Install FFmpeg and ensure it is on PATH |
| Status stuck CONNECTING | Agent not running or not polling; check `EDGE_AGENT_ID` |
| Start button disabled | Camera missing RTSP config or disabled |
| Gray / tiny JPEGs | Raise `EDGE_WARMUP_FRAMES` / `EDGE_MIN_JPEG_BYTES`; ensure continuous decode is running |

Test from the Cameras page (**Test RTSP**) or:

```bash
curl -X POST http://localhost:8000/api/v1/cameras/<id>/test-rtsp \
  -H "Authorization: Bearer <token>"
```

## Tests

```bash
cd edge-agent
pytest
```

Covers URL validation, reconnect backoff, continuous FFmpeg lifecycle (mocked), warm-up / invalid discard, mock frame source, offline failure handling, heartbeat payload shape. Uses fakes — no real CCTV required.
