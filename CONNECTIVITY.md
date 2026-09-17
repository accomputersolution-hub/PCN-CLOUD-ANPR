# Connectivity

PCN Cloud ANPR reaches a customer's NVR over a **private VPN path**. Public inbound RTSP (port 554) is not the default architecture.

## Two supported modes

### Mode 1 — Existing VPN router

```
CCTV → NVR → existing VPN-capable router (MikroTik or other) → secure VPN → PCN Cloud
```

Use this when the site already has a router that can terminate a site-to-site or outbound VPN.

### Mode 2 — PCN Cloud Gateway

```
CCTV → NVR → existing LAN router → PCN Cloud Gateway → secure VPN → PCN Cloud
```

Use this when the current router cannot run the required VPN. The first hardware class is TP-Link ER605-class, but the **application is vendor-agnostic**. Vendor and model are metadata only; there is no ER605 or MikroTik command runner in the core API.

## Site fields

Each site stores:

| Field | Meaning |
| --- | --- |
| `connectivity_mode` | `EXISTING_VPN_ROUTER` or `PCN_CLOUD_GATEWAY` |
| `anpr_deployment_mode` | `LOCAL_EDGE_AGENT` (current) or `CLOUD_VIA_GATEWAY` (future) |
| `primary_gateway_id` | Which enrolled device is the path for that site |

ANPR processing stays on the **Edge Agent** today. The gateway is only the network path.

## Provider model (conceptual)

```
ConnectivityProvider
  ├── ExistingRouterVPN     # customer-owned MikroTik / compatible
  ├── PcnCloudGateway       # PCN-supplied CPE (ER605-class or later)
  └── Future providers      # additional vendors without schema rewrites
```

Capabilities are a JSON map on the gateway row (`wireguard`, `ipsec`, `lan_probe`, …). New vendors add capability flags; they do not fork the API.

## NVR-first cameras

A camera source may be:

- direct IP camera
- NVR channel
- RTSP endpoint
- ONVIF (where supported)

`nvr_id`, `channel`, `source_type`, `gateway_id`, and `anpr_enabled` are optional. Existing RTSP-only cameras keep working with defaults.

## Security

- Operator JWT for CRUD and provision/revoke
- Device headers `X-Gateway-Id` + `X-Gateway-Key` for heartbeat
- Device key bcrypt-hashed; plaintext shown only on create/provision
- Revoke clears the hash and rejects heartbeat
- PWA responses never include RTSP URLs, passwords, or VPN private keys
