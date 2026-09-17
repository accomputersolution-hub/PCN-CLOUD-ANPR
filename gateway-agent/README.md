# PCN Gateway Agent (specification)

This package is the **interface and configuration contract** for a future on-site connectivity agent.

It does **not** establish WireGuard/IPsec, flash ER605 firmware, or talk to MikroTik RouterOS. Those are provider plugins to be added later.

## Intended runtime

- Outbound HTTPS to PCN Cloud (`/api/v1/gateways/{id}/heartbeat`)
- Outbound VPN to the PCN concentrator (provider-specific)
- No inbound public ports for management

## Configuration (`pcn_gateway/config.example.env`)

See the example env file. Secrets stay in environment variables, never in git.

## Interfaces

`pcn_gateway/interfaces.py` defines:

- `VpnProvider` — connect / disconnect / status (MikroTik, generic WireGuard, future ER605)
- `GatewayAgent` — authenticate, heartbeat loop, reconnect
- `LanReporter` — optional subnet/NVR reachability probes

A fake in-process success path is **not** provided for production VPN. Heartbeat against a real enrolled gateway is the only live integration in this phase (backend already implemented).
