# DAN app icon

Selected artwork: warm ivory impressionist v7, approved by the user.
Source: `source-v7.png` (original generated resolution); generation prompt in `prompt-v7.md`. Unselected variants were deleted at the user's request.

- `icon.png`: 1024px desktop source / Linux icon.
- `icon.icns`: macOS icon, standard 16–1024px representations.
- `icon.ico`: Windows icon with 16, 32, 48, 64, 128, and 256px PNG representations.
- `../../public/favicon.png`: 64px browser icon.

Exports preserve the approved artwork, including its ivory tile and white outer margin. Resized with macOS `sips`; ICNS and ICO package PNG entries using Python's standard library. ICNS decoding verified with `iconutil`. No added runtime dependencies.

The local macOS updater preserves the installed app's icon. New desktop packages use these assets; existing installations retain their icon through that updater.
