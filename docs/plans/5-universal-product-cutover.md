# 5: Universal Product Cutover

**Status:** completed
**Goal:** Remove all tracked code and plan history that precedes or sits outside Universal Cell → Universal Organism → Super DAN → Work/Notes GUI.

## Tasks

- [x] Commit and push the complete pre-archive worktree in logical sections.
- [x] Prune legacy frontend, Electron, Python runtime, tests, examples, sample graphs, and scripts.
- [x] Rewire the retained server, CLI, provider, tool, session, and GUI surfaces.
- [x] Replace old plan numbering with the compact 1–5 product roadmap.
- [x] Update user, architecture, API, roadmap, todo, and changelog documentation.
- [x] Run retained Python, frontend, Electron, build, mobile, and product API validation.
- [x] Commit and push the cutover.

## Decisions

- Git history is the archive; obsolete tracked files are deleted rather than copied into an `_archive` directory.
- Ignored local experiments and runtime data are not deleted because they were not part of the pushed snapshot.
- Only direct dependencies of the retained four-layer product remain active.

## Notes

- Pre-archive product commits `90cc14af` and `7972238a` were pushed before deletion began.
- The old ignore policy hid 357 legacy test sources and nine companion assets from that snapshot. They were added and pushed in `8594143c` and `0d630dca` before removal, making `0d630dca` the complete recovery point.
