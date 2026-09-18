"""Diagnose Manual ANPR confirm → events list / dashboard visibility."""
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
    body = json.dumps({"email": "admin@pcncloud.in", "password": "ChangeMe@12345"}).encode()
    st, raw = req("POST", "/auth/login", data=body, headers={"Content-Type": "application/json"})
    print("login", st)
    if st != 200:
        print(raw[:500])
        return 1
    login = json.loads(raw)
    token = login["access_token"]
    user = login["user"]
    print("user", user.get("email"), user.get("role"), "org", user.get("organization_id"), "sites", user.get("site_ids"))

    st, raw = req("GET", "/cameras", token=token)
    cams = json.loads(raw)
    cam = cams[0]
    print("camera", cam["id"], "org", cam["organization_id"], "site", cam["site_id"])

    # Prefer a tiny JPEG if full OCR is slow — still creates real event path
    img = IMAGE.read_bytes() if IMAGE.is_file() else b""
    if not img:
        print("FAIL no image")
        return 1

    boundary = "----PCNDiagBoundary"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"camera_id\"\r\n\r\n{cam['id']}\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"car.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        + img
        + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    st, raw = req(
        "POST",
        "/manual-anpr/analyze",
        token=token,
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    print("analyze", st)
    if st != 200:
        print(raw[:800])
        return 1
    analysis = json.loads(raw)
    plate = analysis.get("detected_plate") or analysis.get("normalized_plate") or "MH20DV2366"
    print("detected", plate, "capture", analysis["capture_id"])

    confirm_body = json.dumps(
        {
            "capture_id": analysis["capture_id"],
            "plate_text": plate,
            "direction": "ENTRY",
            "ocr_confidence": analysis.get("ocr_confidence") or 0.96,
            "plate_confidence": analysis.get("plate_confidence") or 0.7,
            "combined_confidence": analysis.get("combined_confidence") or 0.86,
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
    eid = event["id"]
    print(
        "created",
        eid,
        event.get("plate_normalized"),
        event.get("source_type"),
        event.get("organization_id"),
        event.get("site_id"),
        event.get("timestamp"),
        event.get("local_timestamp"),
        event.get("operator_user_id"),
    )

    st, raw = req("GET", f"/events/{eid}", token=token)
    print("get_event", st, raw[:200] if st != 200 else json.loads(raw).get("plate_normalized"))

    st, raw = req("GET", "/events?page=1&page_size=25", token=token)
    print("list_events", st)
    if st != 200:
        print(raw[:1000])
    else:
        page = json.loads(raw)
        ids = [i["id"] for i in page.get("items") or []]
        plates = [i.get("plate_normalized") for i in page.get("items") or []]
        sources = [i.get("source_type") for i in page.get("items") or []]
        print("total", page.get("meta", {}).get("total"), "ids_head", ids[:5], "plates", plates[:5], "sources", sources[:5])
        print("found_in_list", eid in ids, plate in plates)

    st, raw = req("GET", f"/events?plate={plate}", token=token)
    print("list_by_plate", st)
    if st == 200:
        page = json.loads(raw)
        print("plate_hits", [(i["id"], i.get("source_type")) for i in page.get("items") or []])

    st, raw = req("GET", "/dashboard/summary", token=token)
    print("dashboard", st)
    if st == 200:
        dash = json.loads(raw)
        recent = dash.get("recent_events") or []
        print(
            "recent_count",
            len(recent),
            "plates",
            [e.get("plate_normalized") for e in recent[:8]],
            "found",
            any(e.get("id") == eid for e in recent),
        )
    else:
        print(raw[:800])

    # Raw Firestore doc
    from app.core.config import get_settings
    from app.firebase.admin import get_firebase_admin_app, reset_firebase_admin_cache
    from app.firebase.firestore_client import get_firestore_client

    get_settings.cache_clear()
    reset_firebase_admin_cache()
    get_firebase_admin_app()
    db = get_firestore_client()
    snap = db.collection("anprEvents").document(eid).get()
    print("firestore_exists", snap.exists)
    if snap.exists:
        data = snap.to_dict() or {}
        print(
            "firestore_fields",
            {
                k: data.get(k)
                for k in (
                    "organization_id",
                    "site_id",
                    "source_type",
                    "direction",
                    "plate_normalized",
                    "timestamp",
                    "local_timestamp",
                    "operator_user_id",
                )
            },
        )
        print("timestamp_type", type(data.get("timestamp")).__name__)

        # Try the same query the repo uses
        org = data.get("organization_id")
        try:
            q = (
                db.collection("anprEvents")
                .where("organization_id", "==", org)
                .order_by("timestamp", direction="DESCENDING")
                .limit(25)
            )
            found = False
            for s in q.stream():
                if s.id == eid:
                    found = True
                    break
            print("query_found", found)
        except Exception as exc:
            print("QUERY_ERROR", type(exc).__name__, exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
