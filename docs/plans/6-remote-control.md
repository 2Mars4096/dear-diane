# 6: SSH bootstrap and remote DAN control

**Status:** in-progress
**Goal:** Install a persistent DAN execution service through SSH, then control its Codex, Claude Code, and OpenRouter-backed work from desktop or phone while the original laptop is offline.

## Current private-network implementation

- Mac owns SSH profiles and installation; `ny` relays TCP over the existing alias WireGuard network to `mini` (test host) or `s600` (user testing only).
- Every machine has a separate browser origin/port and authenticated DAN service. HTTP runs only inside the private network; public TLS/account login is a later deployment layer.
- `src/dan/remote/` packages the built web app and Python runtime, installs systemd user services, and checks lingering. Source releases, service credentials, and durable state are separate directories.
- Settings saves SSH alias, optional SSH jump through the relay, private addresses/ports, and workspace; supports inspect, install/update, explicit OpenRouter provisioning, access-key reveal, and opening the remote browser.
- Sessions use existing server stores. Remote project metadata syncs by field patch across browsers; tabs and selection stay per-device. Legacy local project bindings are not migrated.
- Existing WireGuard ownership stays in `~/Downloads/local_projects/alias`; no competing tunnels or VPN units are installed.
- Implementation progress and deployment results are recorded below. Remaining items are intentionally unchecked until verified.

## Longer-term topology

```text
Desktop DAN / phone browser
          | HTTPS + authenticated session
          v
ny: web UI + authentication + machine routing
          | loopback-only reverse SSH forwarding
          v
Execution host: persistent DAN backend + durable stores
          | local subprocesses / provider requests
          +-- Codex app-server (stdio)
          +-- Claude Code headless adapter
          +-- DAN workers using OpenRouter
          +-- project files, tools, processes, credentials
```

- Local SSH access bootstraps the execution host. The execution host owns its subsequent connection to `ny`; laptop-owned tunnels cannot satisfy laptop-off access.
- For the first deployment, `ny` can also be the execution host, removing the tunnel. A separate target needs outbound access to `ny`, or an independently provisioned `ny` → target SSH route. Local-only SSH reachability does not establish either route.
- Keep job/session state authoritative on the execution host. The gateway owns login and the machine directory, not a second scheduler or duplicate transcript store.
- Reuse the same Work/Notes frontend; offer a phone browser first, with optional PWA packaging later.
- Initial scope: one operator, one execution host; leave multi-user sharing and distributed scheduling for later work.

## Evidence collected, 2026-09-22

- `ssh ny` succeeds. Read-only non-interactive inspection found Linux, Python 3, and systemd. Node, Codex, Claude, nginx, Caddy, and Tailscale were not found on that shell's PATH; this does not prove they are absent from all login environments. No listeners on ports 80/443 appeared in the bounded socket listing; firewall, DNS, forwarding policy, and service-management permissions remain unverified.
- `src/dan/server/app.py` composes the API with wildcard CORS and no authentication middleware. Public access must wait for authenticated routing and a route-level access audit.
- `src/dan/server/__main__.py` supports loopback binding and `--no-reload`; the backend can run without Electron.
- `src/dan/native_workers/codex_live.py` already launches `codex app-server --listen stdio://`. Keep the Codex transport local to the execution host.
- `src/dan/native_workers/lead.py` and `service.py` connect native leads to DAN team delegation and provider settings. The user's clarified requirement is independent harness/model selection: [4-4](4-4-agent-model-selection.md) now implements OpenRouter powering Codex/Claude themselves, with separately configured teammates. Remote execution must preserve these same profiles.
- Electron currently starts/proxies a local backend. Project registries and some reader/UI state remain in profile-local storage; remote access alone would not synchronize them.
- Native worker restart-resume and approval transport remain unfinished in [native workers](../UI-plans/2-native-agent-workers.md). Browser disconnect, backend restart, and machine reboot require distinct behavior.
- Official [Codex app-server documentation](https://learn.chatgpt.com/docs/app-server) confirms the stdio integration contract. Pin and capability-check the installed CLI version; no need to expose a Codex WebSocket listener publicly.

## Tasks

- [x] [6-3-remote-project-folders](6-3-remote-project-folders.md) — open existing remote folders through the connected host's project dialog; deployed and verified on mini.

- [x] [6-2-ssh-connection-setup](6-2-ssh-connection-setup.md) — simple Add SSH dialog, manual hosts/ports/identity files, alias suggestions, separate phone setup.

- [ ] [6-1-private-relay](6-1-private-relay.md) — implemented/deployed on mini and ny; direct VPN verified; physical-phone acceptance pending.

- [ ] 1. Package a headless execution service.
  - [x] Version the Python runtime and built frontend; define writable state/workspace locations outside release directories.
  - [ ] Add install/status/upgrade/uninstall operations with rollback; use a supervised service, `--no-reload`, restart backoff, and verified boot persistence under the selected OS account.
  - [x] Discover remote CLI paths/accounts in the actual service environment; authenticate on the execution host and retain credentials there.
- [ ] 2. Establish authenticated remote access before public exposure.
  - [ ] Configure a hostname, TLS, operator login, revocable sessions, CSRF/origin protection, and explicit host authorization for every API/file/event route.
  - [ ] Keep DAN and tunnel listeners on loopback; route all access through the authenticated gateway and prevent direct backend bypass.
  - [ ] Provision a dedicated per-host relay identity with pinned host keys and forwarding restricted to its assigned loopback port; no general shell or agent forwarding.
  - [ ] Supervise the host-owned tunnel, with health checks/reconnect backoff; show offline state without replaying mutation requests.
  - [ ] Verify proxy streaming, timeout behavior, and event reconnection; define the gateway as a trusted component able to see proxied content.
- [ ] 3. Add desktop remote connections.
  - [x] SSH alias → inspect host → install service → select remote workspace → pair gateway; show actionable missing-runtime/login feedback.
  - [x] Add a connection abstraction shared by API, file, event, and upload clients; scope all resource IDs by stable machine ID (separate browser origin per machine).
  - [x] Disable local file-path operations for remote resources; use remote browsing/download/upload and a visible machine/workspace label.
- [ ] 4. Make sessions consistent across devices.
  - [ ] Move project registry and necessary chat bindings to server storage with migration from browser-local records.
  - [ ] Reattach streams by durable cursor with deduplication and idempotent command submission; concurrent clients must not start the same turn twice.
  - [ ] Persist approvals scoped to machine/run/action and enforce one-time resolution; unsupported native approval capabilities remain unavailable.
  - [ ] Keep jobs alive across browser/laptop disconnect; mark backend-crashed jobs interrupted and resume only where the adapter supports it.
- [ ] 5. Validate the first complete deployment.
  - [ ] Run a native Codex task, a Claude task, and a Codex-lead/OpenRouter-team task on the execution host.
  - [ ] Disconnect the bootstrap laptop; use a phone over cellular to see progress, send a follow-up, resolve a supported approval, and stop a run.
  - [ ] Reconnect after a tunnel outage without duplicate output/commands; reboot the host and verify service availability plus truthful interrupted-run state.
  - [ ] Test rejected unauthenticated requests, expired/revoked sessions, forged origins, wrong-host routing, and workspace path escapes.
  - [ ] Verify phone-width conversation, machine selection, attachments, approvals, and reconnect state; record costs and runtime versions for live model checks.

## Decisions

- Add this as deployment/connection support around the current Agent V2 runtime, preserving the existing execution stack.
- Prefer moving the agent harness to the remote machine. A harness running on a laptop remains laptop-dependent even if its shell commands use SSH.
- Keep SSH private keys and model credentials out of browser storage. Do not copy the laptop's whole credential directories during installation.
- Implement the persistent remote backend and authenticated browser path first; automate desktop bootstrap after that path passes the laptop-off acceptance test.

## Deployment inputs

- User confirmed `ny` relay, `mini` execution test, and `s600` for user testing. Both hosts use existing WireGuard aliases.
- No public hostname supplied: first browser path uses the private VPN address on `ny`. Public TLS remains unconfigured.
- Mac is the setup/credential authority; phone needs remote access credentials and connection information, with no laptop tunnel dependency.

## Notes

- 2026-09-23: implementation and deployment testing underway. Mini already has Python 3.11+, Node 22, Codex 0.156.1, and lingering. Enabled ny user lingering; no VPN configuration changed.
