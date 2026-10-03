# Dear Diane app icon

Selected artwork: coral walkie-talkie on warm ivory impasto, approved by the user.
Source: `source-v7.png` (original generated resolution); generation prompt in `prompt-v7.md`. Unselected variants were deleted at the user's request.

- `icon.png`: 1024px desktop source / Linux icon.
- `icon.icns`: macOS icon, standard 16–1024px representations.
- `icon.ico`: Windows icon with 16, 32, 48, 64, 128, and 256px PNG representations.
- `../../public/favicon.png`: 64px browser icon.

Exports preserve the approved artwork, including its ivory tile and white outer margin. Resized with macOS `sips`; ICNS and ICO package PNG entries using Python's standard library. ICNS decoding verified with `iconutil`. No added runtime dependencies.

The local macOS updater preserves the installed app's icon. New desktop packages use these assets; existing installations retain their icon through that updater.

## Menu bar and new concepts

The menu bar uses `trayTemplate.png` and `trayTemplate@2x.png` (18pt/36px), a matching monochrome radio silhouette, separately from the painterly app artwork. Editable geometry is in `trayTemplate.svg`; `render-tray.swift` regenerates the two PNG representations. Electron marks it as a macOS template image.

`concepts/diane-flowing-d.png` and `concepts/diane-reading-companion.png` are built-in imagegen proposals; prompts are in `concepts/prompts.md`. The coral walkie-talkie supersedes these explorations.

Final imagegen prompt: Export the selected left app icon as one square; preserve the coral radio, blue antenna, jade side button, three ivory speaker slots, golden control, ivory tile and impasto texture. Remove the separate menu mark and presentation space. Center with a small even white margin; no redesign or added elements.
