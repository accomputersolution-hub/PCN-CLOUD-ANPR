"""Manual ANPR latency smoke: cold then warm analyze (no confirm / no Firestore write)."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IMAGE = Path(r"C:\Users\mdsal\Downloads\car.jpg")
BASE = "http://127.0.0.1:8000/api/v1"


def req(method: str, path: str, token: str | None = None, data: bytes | None = None, headers: dict | None = None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(request, timeout=600) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def analyze(token: str, cam_id: str, img: bytes) -> tuple[int, dict, float]:
    boundary = "----PCNManualBoundaryWarm"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"camera_id\"\r\n\r\n{cam_id}\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"car.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        + img
        + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    payload = b"".join(parts)
    t0 = time.perf_counter()
    st, raw = req(
        "POST",
        "/manual-anpr/analyze",
        token=token,
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    elapsed = (time.perf_counter() - t0) * 1000.0
    body = json.loads(raw) if raw else {}
    return st, body, elapsed


def main() -> int:
    if not IMAGE.is_file():
        print("FAIL: missing", IMAGE)
        return 1

    body = json.dumps({"email": "admin@pcncloud.in", "password": "ChangeMe@12345"}).encode()
    st, raw = req("POST", "/auth/login", data=body, headers={"Content-Type": "application/json"})
    if st != 200:
        print("FAIL login", st, raw[:300])
        return 1
    token = json.loads(raw)["access_token"]

    st, raw = req("GET", "/cameras", token=token)
    cams = json.loads(raw)
    if st != 200 or not cams:
        print("FAIL cameras", st, raw[:200])
        return 1
    cam_id = cams[0]["id"]
    img = IMAGE.read_bytes()

    # Cancel any pending capture after each analyze so we don't leak pending files
    def cancel(capture_id: str) -> None:
        boundary = "----PCNCancel"
        payload = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"capture_id\"\r\n\r\n"
            f"{capture_id}\r\n--{boundary}--\r\n"
        ).encode()
        req(
            "POST",
            "/manual-anpr/cancel",
            token=token,
            data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    st1, a1, ms1 = analyze(token, cam_id, img)
    print("cold_status", st1)
    if st1 != 200:
        print(a1)
        return 1
    cancel(a1["capture_id"])
    print(
        "cold_ms",
        round(ms1, 1),
        "processing_ms",
        a1.get("processing_ms"),
        "plate",
        a1.get("detected_plate"),
        "ocr",
        a1.get("ocr_confidence"),
    )

    st2, a2, ms2 = analyze(token, cam_id, img)
    print("warm_status", st2)
    if st2 != 200:
        print(a2)
        return 1
    cancel(a2["capture_id"])
    print(
        "warm_ms",
        round(ms2, 1),
        "processing_ms",
        a2.get("processing_ms"),
        "plate",
        a2.get("detected_plate"),
        "ocr",
        a2.get("ocr_confidence"),
    )
    print("LATENCY_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
