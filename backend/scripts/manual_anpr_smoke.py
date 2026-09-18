"""One-shot live Manual ANPR smoke (Firestore). Cleans up created docs/objects."""
from __future__ import annotations

import json
import sys
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
        with urllib.request.urlopen(request, timeout=300) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    if not IMAGE.is_file():
        print("FAIL: missing", IMAGE)
        return 1

    # login
    body = json.dumps({"email": "admin@pcncloud.in", "password": "ChangeMe@12345"}).encode()
    st, raw = req("POST", "/auth/login", data=body, headers={"Content-Type": "application/json"})
    if st != 200:
        print("FAIL login", st, raw[:300])
        return 1
    login = json.loads(raw)
    token = login["access_token"]
    print("login_ok", login["user"]["role"])

    # cameras
    st, raw = req("GET", "/cameras", token=token)
    if st != 200:
        print("FAIL cameras", st, raw[:300])
        return 1
    cams = json.loads(raw)
    if not cams:
        print("FAIL no cameras")
        return 1
    cam_id = cams[0]["id"]
    print("camera", cam_id, cams[0].get("name"))

    # multipart analyze
    boundary = "----PCNManualBoundary7MA4YWxkTrZu0gW"
    img = IMAGE.read_bytes()
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"camera_id\"\r\n\r\n{cam_id}\r\n".encode())
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"car.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        + img
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    payload = b"".join(parts)
    st, raw = req(
        "POST",
        "/manual-anpr/analyze",
        token=token,
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    print("analyze", st)
    if st != 200:
        print(raw[:800])
        return 1
    analysis = json.loads(raw)
    print(
        "plate",
        analysis.get("detected_plate"),
        "ocr",
        analysis.get("ocr_confidence"),
        "ms",
        analysis.get("processing_ms"),
        "event_created",
        analysis.get("event_created"),
    )
    assert analysis["event_created"] is False

    # confirm ENTRY
    confirm_body = json.dumps(
        {
            "capture_id": analysis["capture_id"],
            "plate_text": analysis["detected_plate"] or analysis["normalized_plate"],
            "direction": "ENTRY",
            "ocr_confidence": analysis["ocr_confidence"],
            "plate_confidence": analysis["plate_confidence"],
            "combined_confidence": analysis["combined_confidence"],
        }
    ).encode()
    st, raw = req(
        "POST",
        "/manual-anpr/confirm",
        token=token,
        data=confirm_body,
        headers={"Content-Type": "application/json"},
    )
    print("confirm", st)
    if st != 200:
        print(raw[:800])
        return 1
    event = json.loads(raw)
    print(
        "event",
        event["id"],
        event["plate_normalized"],
        event["direction"],
        event["source_type"],
        "operator",
        event.get("operator_user_id"),
        "snap",
        event.get("snapshot_path"),
        "crop",
        event.get("plate_crop_path"),
    )
    assert event["source_type"] == "MANUAL"
    assert event.get("snapshot_path")
    assert "rtsp" not in raw.lower()

    # verify storage bytes exist via API
    snap_key = event["snapshot_path"]
    st, _ = req("GET", f"/storage/{snap_key}", token=token)
    print("storage_snapshot", st)

    # cleanup Firestore + storage
    from app.core.config import get_settings
    from app.firebase.admin import get_firebase_admin_app, reset_firebase_admin_cache
    from app.repositories import anpr_event_repo, vehicle_repo
    from app.services.storage import get_storage, reset_storage_cache
    import asyncio

    get_settings.cache_clear()
    reset_firebase_admin_cache()
    reset_storage_cache()
    get_firebase_admin_app()

    async def cleanup() -> None:
        ev = await anpr_event_repo().get(event["id"])
        store = get_storage()
        for key in filter(
            None,
            [
                event.get("snapshot_path"),
                event.get("plate_crop_path"),
                event.get("vehicle_crop_path"),
            ],
        ):
            try:
                store.delete(key)
            except Exception:
                pass
        if ev is not None:
            from app.firebase.firestore_client import get_firestore_client

            get_firestore_client().collection("anprEvents").document(ev.id).delete()
            if ev.vehicle_id:
                get_firestore_client().collection("vehicles").document(ev.vehicle_id).delete()

    asyncio.run(cleanup())
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
