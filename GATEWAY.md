# Gateway

The **PCN Cloud Gateway** is a connectivity appliance. It is not the ANPR Edge Agent.

| | Edge Agent | Gateway |
| --- | --- | --- |
| Job | RTSP, FFmpeg, frames, PaddleOCR, temporal confirm, cooldown, events | Outbound VPN, LAN reachability to NVR, health |
| Runs | On-site PC/NUC **or** later a cloud worker using the VPN path | On-site CPE (ER605-class or other) or an existing VPN router enrolled as a device |
| Auth | `X-Edge-Id` / `X-Edge-Key` | `X-Gateway-Id` / `X-Gateway-Key` |
| This phase | Fully implemented | Data model, APIs, heartbeat, UI, agent **specification** |

This repository does **not** automate TP-Link ER605 or MikroTik CLI. Vendor/model fields are inventory.

## Device record

- `gateway_id`, `organization_id`, `site_id`
- `device_type`: `EXISTING_VPN_ROUTER` \| `PCN_CLOUD_GATEWAY`
- `vendor`, `model`, `firmware_version` (free text)
- `vpn_status`, `health_status`, `provisioning_status`, `last_seen`
- `lan_subnet`, `capabilities` JSON
- `device_key_hash` (never returned)
- `revoked_at`, `is_active`

## Operator APIs (`/api/v1`)

| Method | Path | Auth |
| --- | --- | --- |
| POST | `/gateways` | JWT `gateway:write` — returns `device_key` once |
| GET | `/gateways` | JWT `gateway:read` |
| GET | `/gateways/{id}` | JWT |
| PATCH | `/gateways/{id}` | JWT |
| DELETE | `/gateways/{id}` | JWT — decommission + revoke |
| POST | `/gateways/{id}/provision` | JWT `gateway:provision` — rotates key |
| POST | `/gateways/{id}/revoke` | JWT — heartbeat fails afterwards |
| POST | `/gateways/{id}/heartbeat` | device headers |
| GET/PATCH | `/sites/{site_id}/connectivity` | JWT |

Heartbeat is outbound from the device. Cloud does not require inbound TCP 554 or a public management port on the gateway.

## Gateway agent (future software)

See `gateway-agent/`. The agent should:

1. Register using the one-time device key (already created by an org admin)
2. Authenticate every heartbeat
3. Establish VPN according to configured **provider plugin** (not hard-coded ER605)
4. Report LAN/subnet and health
5. Reconnect after WAN loss
6. Never require inbound public access for management

ER605/WireGuard deployment work remaining: provider plugin, VPN server, bootstrap image, hardware QA. None of that is claimed as implemented here.
