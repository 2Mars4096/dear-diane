# Dear Diane app icon

Selected artwork: coral walkie-talkie on a white painted background, without a raised outer rim, approved by the user.
Source: `source-radio-painted-crop.png`; the previous white-margin export is retained as `source-walkie-talkie.png`.

- `icon.png`: 1024px desktop source / Linux icon.
- `icon.icns`: macOS icon, standard 16–1024px representations.
- `icon.ico`: Windows icon with 16, 32, 48, 64, 128, and 256px PNG representations.
- `../../public/favicon.png`: 64px browser icon.

Exports preserve the approved artwork, with its white painted background intact, a flat rounded-square boundary, and transparent exterior corners. Resized with macOS `sips`; ICNS and ICO package PNG entries using Python's standard library. ICNS decoding verified with `iconutil`. No added runtime dependencies.

The local macOS updater preserves the installed app's icon. New desktop packages use these assets; existing installations retain their icon through that updater. The user-selected coral release was installed with an explicit staged-icon replacement and signature verification, so this installation now uses the coral artwork.

## Menu bar and new concepts

The menu bar uses `trayTemplate.png` and `trayTemplate@2x.png` (18pt/36px), a matching monochrome radio silhouette, separately from the painterly app artwork. Editable geometry is in `trayTemplate.svg`; `render-tray.swift` regenerates the two PNG representations. Electron marks it as a macOS template image.

`concepts/diane-flowing-d.png` and `concepts/diane-reading-companion.png` are built-in imagegen proposals; prompts are in `concepts/prompts.md`. The coral walkie-talkie supersedes these explorations.

Final imagegen prompt: Export the selected left app icon as one square; preserve the coral radio, blue antenna, jade side button, three ivory speaker slots, golden control, ivory tile and impasto texture. Remove the separate menu mark and presentation space. Center with a small even white margin; no redesign or added elements.

Background-removal prompt (built-in imagegen): Remove only the plain white exterior and outer shadow; preserve the ivory painted tile and coral radio. Real alpha transparency, clean antialiased tile boundary, no redesign or color changes.

Cutout prompt (built-in imagegen): Remove the entire ivory tile. Keep only the coral radio, blue antenna, jade side button, gold knob and cream speaker slots. True alpha outside the radio silhouette; no frame, plate, rim or halo.

Current correction prompt (built-in imagegen): KEEP the white painted background behind the radio. Remove only the raised rim, bevel, lip and shadow; white paint extends to a single rounded-square boundary. Preserve the radio colors and layout. Transparency only outside the square.

Final export: center-crop the existing 1254px white-painted source to 1080px using macOS sips. The crop removes exterior presentation padding, keeps the radio intact, and carries the paint texture to the icon edges. No further generation.
