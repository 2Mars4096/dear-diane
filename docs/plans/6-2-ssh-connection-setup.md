# 6-2: Simple SSH connection setup

**Parent:** [6-remote-control](6-remote-control.md)
**Status:** completed
**Goal:** Add an SSH host through a short Codex-style form, independently of private relay setup.

## Tasks
- [x] Inspect the installed Codex form: display name, hostname, optional port, config/agent or identity file, Save/Cancel.
- [x] Put Add SSH connection beside the section heading; use a focused dialog and suggest literal SSH aliases from config/Include files.
- [x] Save/check manual user@host profiles without requiring VPN addresses; generate unique IDs and guard accidental create-overwrite.
- [x] Apply optional port and identity path consistently to inspection, SSH install, and SCP; keep relay SSH settings independent.
- [x] Preserve legacy installed profiles/defaults and keep phone-access/relay configuration separate.
- [x] Verify 9 backend tests, 4 UI tests, build budgets, and desktop/390px browser save, reopen, and identity-path persistence. No real remote host contacted or provisioned.
- [x] Install the signed local update with rollback and no-active-work checks; desktop proxy health and installed alias endpoint pass (20 aliases discovered).

## Compact connection list, 2026-09-28
- [x] Replace verbose connection blocks with one host/status row and three icon actions. Keep Add visible.
- [x] Move SSH properties, edit/check, phone settings, key reveal, and collapsed installation controls into a native details dialog. Retain explicit key provisioning and truthful checked status.
- [x] Five focused tests, production build/budgets, and dark desktop/390px browser layout and Escape checks pass.
- [x] Install the verified desktop update and confirm archive equality, signature, and app-owned backend/proxy health. Rollback retained.

## Decisions
- Mirror the observed Codex interaction, implementing DAN-specific fields and behavior independently. No proprietary source added to this repo.
- Existing aliases inherit SSH configuration unless explicitly overridden. Keys stay on the Mac; no private key contents are read or returned by alias discovery.
- SSH-only profiles support saving and checking connectivity. Persistent remote DAN/phone access still needs the existing private relay deployment; this change does not add a direct SSH tunnel or public gateway.
- First-contact host trust and password/passphrase setup remain in Terminal/SSH agent. Keep strict known-host checks and noninteractive SSH.
- Saving phone-access changes does not stop/uninstall existing services. Install/update remains a separate explicit action.
