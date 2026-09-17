# Remaining work after Milestone 1

See ARCHITECTURE.md. Next phases:

- Phase 5: live RTSP/OpenCV capture, ONVIF probe, real camera test
- Phase 6: PaddleOCR (Apache 2.0) and a commercially licensed detector behind the existing interfaces
- Phase 7: scheduled retention, richer camera health alerts, PDF/Excel exporters
- Firebase-first runtime: Auth + Firestore + Storage on `pcn-anpr` (no PostgreSQL required). See FIREBASE_MIGRATION.md / SETUP.md
- Gateway hardware: WireGuard/IPsec concentrator, ER605-class bootstrap image, MikroTik provider plugin (APIs exist; no vendor automation yet)
