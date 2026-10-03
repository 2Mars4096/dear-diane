# Dear Diane internal-name migration

Status: source-path rename implemented and desktop installed; runtime/profile migration remains separate.

## Names
- Product: Dear Diane. Short name: Diane.
- Python package/commands: `diane`, `diane-serve`, `dear-diane`.
- Environment prefix: `DIANE_`.
- New desktop profile: `Application Support/diane`; new bundle ID: `com.diane.desktop`.

## Scope and order
1. Add the new Python/CLI names with explicit old-import aliases. Verify both import paths resolve to the same classes. Switch the desktop launcher only after these checks pass.
2. Add environment lookup precedence: new names first, old names as read-only compatibility. Do not modify credential values or print them. Update installation documentation/examples.
3. Copy the closed desktop profile to the new directory, keeping the old profile as rollback. Compare counts/hashes for chats, note bodies, attachments, account configuration and reading sessions before launching.
4. Migrate browser storage keys with a versioned, idempotent mapping before stores initialize. Preserve values and original keys until migration validation finishes. Do not replace words inside user notes, prompts, messages, saved source files or arbitrary JSON strings.
5. Add explicit schema/backend-ID compatibility adapters before new writes use new identifiers. Update IPC endpoints on both ends together. Keep remote services on their existing identifiers until their own migration is tested.
6. Change bundle/update identity with acceptance of the old signed identity during the transition. Verify install, rollback and the next update on a copied profile.
7. Remove temporary aliases only after old data/imports can be read without them. Record unavoidable historical identifiers in one migration module rather than presenting them in the UI.

## Acceptance checks
- New/old import identity, CLI startup, environment precedence, schema reads and remote-profile compatibility.
- Migration runs twice without changing data again; missing/failed copy leaves the original untouched.
- Existing reading tabs, six reviewed quotes, note bodies, chat context and accounts survive a full app restart.
- Frontend/backend tests, production bundle limits, signature and installed archive equality.
- Normal push in separate commits; no history rewrite, destructive global replacement, or deletion of the old profile.

## Earlier review boundary (reader release)
The attempted broad replacement was stopped and its partial changes undone. At that point, no Python package, stored identifier, profile directory or bundle identity had been renamed. The reader fixes and visible log/update naming cleanup are independent and can ship now.

## Source-path rename (2026-10-03)
- [x] Move `src/dan` to `src/diane`; update Python imports, dynamic module names, launcher and packaging references.
- [x] Rename settings component, evaluation scripts, phone icon, WireGuard helper and Android Kotlin directories; update callers.
- [x] Publish new `diane` CLI names; retain existing command aliases pointing at the new implementation.
- [x] Validate Python tests, frontend tests/build, Flutter widget test and installed launch.
- [ ] Complete optional Android APK build (dependency downloads in progress).
- [x] Commit by feature; push after final documentation update.

This step renames repository source paths. Runtime profiles, environment variables, schema IDs and historical worktrees stay compatible. Android uses the new Kotlin namespace with the existing application ID and preferences. Python callers must update imports to `diane`; old module imports are not retained as a second source folder.

Validation: 855 Python tests pass across the main run and sandbox-capable rerun (four skipped); 437 frontend tests and one Flutter widget test pass. All 19 installed command entry points resolve. Source audit covers 187 Python modules; no tracked path contains the old name. Lockfile check and desktop health/signature/archive checks pass.
