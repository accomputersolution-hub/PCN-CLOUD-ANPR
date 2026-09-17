"""Firebase-first live smoke (pcn-anpr). Disposable data only.

Requires FIREBASE_CREDENTIALS_FILE / Admin config.
Does NOT use PostgreSQL.
Creates tagged smoke documents, verifies Storage, deletes afterward.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SMOKE = f"ff-smoke-{uuid.uuid4().hex[:8]}"
CRED_CANDIDATES = [
    ROOT / "secrets" / "pcn-anpr-firebase-adminsdk-fbsvc-602a912636.json",
    ROOT / "secrerts" / "pcn-anpr-firebase-adminsdk-fbsvc-602a912636.json",
]


def _configure() -> None:
    cred = next((p for p in CRED_CANDIDATES if p.is_file()), None)
    if cred is None and not (os.environ.get("FIREBASE_CREDENTIALS_FILE") or "").strip():
        raise SystemExit("FAIL: set FIREBASE_CREDENTIALS_FILE or place Admin JSON under backend/secrets/")
    os.environ.setdefault("AUTH_PROVIDER", "firebase")
    os.environ.setdefault("DATASTORE_PROVIDER", "firestore")
    os.environ.setdefault("STORAGE_PROVIDER", "firebase")
    os.environ.setdefault("FIREBASE_PROJECT_ID", "pcn-anpr")
    os.environ.setdefault("FIREBASE_STORAGE_BUCKET", "pcn-anpr.firebasestorage.app")
    if cred is not None:
        os.environ["FIREBASE_CREDENTIALS_FILE"] = str(cred)
    os.environ.pop("DATABASE_URL", None)


def main() -> int:
    _configure()
    from app.core.config import get_settings
    from app.firebase.admin import get_firebase_admin_app, reset_firebase_admin_cache
    from app.domain.records import (
        AnprEventRecord,
        CameraRecord,
        GateRecord,
        OrganizationRecord,
        SiteRecord,
        UserProfileRecord,
        VehicleRecord,
    )
    from app.domain.connectivity import GatewayRecord, NvrRecord
    from app.repositories import (
        anpr_event_repo,
        camera_repo,
        gate_repo,
        gateway_repo,
        nvr_repo,
        organization_repo,
        site_repo,
        user_profile_repo,
        vehicle_repo,
    )
    from app.services.storage import FirebaseStorage, reset_storage_cache

    get_settings.cache_clear()
    reset_firebase_admin_cache()
    reset_storage_cache()
    s = get_settings()
    assert s.datastore_provider == "firestore"
    assert s.auth_provider == "firebase"
    assert s.storage_provider == "firebase"
    print("providers", s.auth_provider, s.datastore_provider, s.storage_provider)
    print("Initializing Admin…")
    get_firebase_admin_app()

    org_id = str(uuid.uuid4())
    site_id = str(uuid.uuid4())
    gate_id = str(uuid.uuid4())
    gw_id = str(uuid.uuid4())
    nvr_id = str(uuid.uuid4())
    cam_id = str(uuid.uuid4())
    veh_id = str(uuid.uuid4())
    evt_id = str(uuid.uuid4())
    uid = f"smoke-uid-{uuid.uuid4().hex[:10]}"
    created = []

    try:
        awaitables = []

        async def run() -> None:
            await organization_repo().add(
                OrganizationRecord(id=org_id, name=f"Smoke Org {SMOKE}", slug=f"smoke-{SMOKE[-6:]}", retention_days=7)
            )
            created.append(("organizations", org_id))
            await site_repo().add(
                SiteRecord(id=site_id, organization_id=org_id, name=f"Smoke Site {SMOKE}", address="smoke")
            )
            created.append(("sites", site_id))
            await gate_repo().add(
                GateRecord(id=gate_id, organization_id=org_id, site_id=site_id, name="Smoke Gate", mode="ENTRY")
            )
            created.append(("gates", gate_id))
            await gateway_repo().add(
                GatewayRecord(
                    id=gw_id,
                    organization_id=org_id,
                    site_id=site_id,
                    name="Smoke GW",
                    device_type="EXISTING_VPN_ROUTER",
                )
            )
            created.append(("gateways", gw_id))
            await nvr_repo().add(
                NvrRecord(id=nvr_id, organization_id=org_id, site_id=site_id, name="Smoke NVR", host="10.0.0.1")
            )
            created.append(("nvrs", nvr_id))
            await camera_repo().add(
                CameraRecord(
                    id=cam_id,
                    organization_id=org_id,
                    site_id=site_id,
                    gate_id=gate_id,
                    name="Smoke Cam",
                    camera_code="SMOKE1",
                    direction="ENTRY",
                    has_credentials=False,
                )
            )
            created.append(("cameras", cam_id))
            now = datetime.now(UTC)
            await vehicle_repo().add(
                VehicleRecord(
                    id=veh_id,
                    organization_id=org_id,
                    plate_normalized="KA01SM9999",
                    first_seen=now,
                    last_seen=now,
                )
            )
            created.append(("vehicles", veh_id))
            keys = {
                "snapshot": f"{org_id}/events/{evt_id}/snapshot.jpg",
                "plate_crop": f"{org_id}/events/{evt_id}/plate_crop.jpg",
                "vehicle_crop": f"{org_id}/events/{evt_id}/vehicle_crop.jpg",
            }
            await anpr_event_repo().add(
                AnprEventRecord(
                    id=evt_id,
                    organization_id=org_id,
                    site_id=site_id,
                    gate_id=gate_id,
                    camera_id=cam_id,
                    vehicle_id=veh_id,
                    direction="ENTRY",
                    plate_text="KA01SM9999",
                    plate_normalized="KA01SM9999",
                    timestamp=now,
                    local_timestamp=now,
                    snapshot_storage_key=keys["snapshot"],
                    plate_crop_storage_key=keys["plate_crop"],
                    vehicle_crop_storage_key=keys["vehicle_crop"],
                    source_type="MOCK",
                )
            )
            created.append(("anprEvents", evt_id))
            await user_profile_repo().add(
                UserProfileRecord(
                    id=uid,
                    email=f"{SMOKE}@example.invalid",
                    full_name="Smoke User",
                    role="VIEWER",
                    organization_id=org_id,
                    firebase_uid=uid,
                    site_ids=[site_id],
                )
            )
            created.append(("users", uid))

            # ownership checks
            assert (await organization_repo().get(org_id)).slug.startswith("smoke-")
            assert (await site_repo().get(site_id)).organization_id == org_id
            assert (await camera_repo().get(cam_id)).organization_id == org_id
            evt = await anpr_event_repo().get(evt_id)
            assert evt is not None
            assert evt.snapshot_storage_key.endswith("snapshot.jpg")
            assert not hasattr(evt, "snapshot_bytes") or getattr(evt, "snapshot_bytes", None) is None

            store = FirebaseStorage()
            for key in keys.values():
                store.save(key, b"smoke-bytes", "image/jpeg")
                assert store.get_bytes(key) == b"smoke-bytes"
                store.delete(key)
            print("OK Storage upload/download/delete")

            # cleanup Firestore
            for collection, doc_id in reversed(created):
                from app.firebase.firestore_client import get_firestore_client
                import asyncio

                def _del(c=collection, d=doc_id):
                    get_firestore_client().collection(c).document(d).delete()

                await asyncio.to_thread(_del)
            print("OK Firestore cleanup", len(created), "docs")

        import asyncio

        asyncio.run(run())
        print("FIREBASE_FIRST_SMOKE_PASSED", SMOKE)
        return 0
    except Exception as exc:
        print("FAIL", exc)
        # best-effort cleanup
        try:
            from app.firebase.firestore_client import get_firestore_client

            db = get_firestore_client()
            for collection, doc_id in created:
                db.collection(collection).document(doc_id).delete()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
