# Animation Workspace

Self-contained HTML canvas animations. Open any `.html` file directly in a browser.

## Files

- **index.html** — Match burning in the dark (canvas particle system: flame, sparks, smoke, embers).
- **network.html** — Deep agent network pulse (nodes, edges, traveling packets, ambient drift).
- **storybook.html** — Pip and Daxter’s Magical Meadow Quest (comic/story panels).
- **arena.html** — Neon Arena (glowing rings, orbiting particles, energy core, radial spikes).
- **index-kimi-k26.html** / **arena-kimi-k26.html** — Variant experiments from prior runs.

## How to run

No build step required. From this directory:

```bash
open index.html
# or
open network.html
```

Or serve the folder with any static server:

```bash
python -m http.server 8000
# then visit http://localhost:8000/network.html
```

## Notes

- All animations are fullscreen and responsive to window resize.
- Each file is self-contained (no external assets or bundler needed).
