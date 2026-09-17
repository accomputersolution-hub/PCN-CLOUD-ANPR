# Remaining work after Milestone 1

See ARCHITECTURE.md. Next phases:

- Phase 5: live RTSP/OpenCV capture, ONVIF probe, real camera test
- Phase 6: PaddleOCR (Apache 2.0) and a commercially licensed detector behind the existing interfaces
- Phase 7: scheduled retention, richer camera health alerts, PDF/Excel exporters
- Firebase Phase 1–2: abstractions done; optional `STORAGE_PROVIDER=firebase` for evidence blobs (events stay on PostgreSQL). Next: Firestore dual-write (Phase 3) — see FIREBASE_MIGRATION.md
- Gateway hardware: WireGuard/IPsec concentrator, ER605-class bootstrap image, MikroTik provider plugin (APIs exist; no vendor automation yet)
