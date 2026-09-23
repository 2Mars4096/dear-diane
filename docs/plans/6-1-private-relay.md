# 6-1: Mac-managed private relay

**Parent:** [6-remote-control](6-remote-control.md)
**Status:** in-progress
**Goal:** Save remote profiles on the Mac and operate mini from a signed-in browser through the existing ny VPN relay.

## Tasks
- [x] Save SSH alias, relay, private endpoints, workspace, and package-source profiles in Settings.
- [x] Inspect host tools; install/update versioned Python/frontend releases with durable state outside releases.
- [x] Provision only explicitly selected OpenRouter credentials on the execution host; keep native accounts there.
- [x] Protect HTTP/files/static assets/WebSockets with machine-specific login and origin checks.
- [x] Use separate browser origins per host, shared project metadata, and remote session-list refresh.
- [x] Configure persistent user services and verify enabled units, lingering, service restart, and retained data.
- [x] Verify actual mini native Codex Astra/medium/fast, Codex/OpenRouter DeepSeek, and Claude/OpenRouter DeepSeek replies after the submitting connection closes.
- [x] Verify phone-width remote UI, independent-browser project updates, authenticated WebSocket reconnect, and rejected remote SSH-setup calls.
- [x] User enabled ny's VPN-only firewall rule; direct authenticated VPN access and phone-width UI verified.
- [ ] Verify a physical phone over its VPN/cellular connection with the Mac offline.
- [ ] User tests s600; this session does not connect to it.

## Deployment record
- Mac is the setup authority. Profiles/access keys live in ignored mode-0600 `graphs/remote_connections/profiles.json`; phone sign-in uses a separate expiring session. No provider key is copied to the phone or relay.
- `mini`: execution `10.77.77.3:18765`; `ny`: relay `10.77.77.1:8765`; browser address `http://10.77.77.1:8765`.
- `s600`: saved but untested profile; proposed execution `10.77.77.4:8765`, relay port `8766`.
- `dan-execution-mini.service` and `dan-relay-mini.service` are active, enabled, and `Linger=yes`. Both restarted successfully. Full machine reboot was not performed.
- Mini release: `~/.local/share/dan-remote/mini/releases/20260923140615-5b7df7`; persistent state: sibling `state/`; credentials: sibling `access.json`/`provider.env` (0600).
- Mini versions: Python 3.11.16, Codex 0.156.1, Claude Code 2.1.66. Tsinghua package mirror completed installation after slow PyPI downloads; mirror is recorded in mini's profile.
- Native Codex and both OpenRouter harness checks returned `REMOTE_MINI_OK`. Initial probes used the legacy `codex` adapter; corrected checks use `native_codex`, matching the Workbench. Native adapter token/cost totals were not reported, so no cost estimate is claimed.
- After the user enabled the VPN-only firewall rule, direct VPN API and authenticated 360px browser checks passed without the SSH SOCKS proxy. The temporary proxy was removed; services need no laptop tunnel. Cross-device new-session visibility also passed.
- Screenshots: `output/playwright/remote-settings-{desktop,phone}.png`, `mini-remote-phone-sessions.png`, `mini-remote-second-browser.png`.

## Applied firewall rule

The user ran from the Mac (ny's adam account requires interactive sudo):

```sh
ssh -t ny 'sudo ufw allow in on wgny proto tcp from 10.77.77.0/24 to 10.77.77.1 port 8765'
```

- This permits existing WireGuard peers only; the relay remains bound to the VPN address.
- `sudo -n` and root SSH were unavailable; the user applied this rule and direct VPN verification passed. Existing VPN configuration was retained.
- A later s600 deployment needs its own VPN-only rule for relay port 8766.

## Limits
- Public HTTPS/account login, phone synchronization of the entire Mac connection directory, individual session revocation, uninstall UI, and native approval/restart-resume capabilities remain in the parent plan.
- Browser disconnect does not stop execution. Backend restart can interrupt running work; existing recovery records reflect that. No live run was interrupted by restart checks.
- Existing Mac legacy project/thread bindings remain local. New remote sessions share their server-side project ID.
