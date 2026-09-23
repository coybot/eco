# Regenerating the autonomy explainer GIFs

The three animations in the root `README.md` are rendered from `scenes.html` — a single
self-contained page of CSS animations (`offset-path` + `@keyframes`, no JS, no images). Open it
directly in a browser to see them live; GitHub can't run CSS, which is why the README embeds
baked GIFs instead.

## Rebuild

```bash
npm install puppeteer@23        # brings its own Chrome for Testing build
node capture.js                 # writes frames/<scene>/NNNN.png
./encode.sh                     # writes out/<scene>.gif
```

Then copy `out/scene{1,2,3}.gif` over `../01-waypoints.gif`, `../02-vehicle-decides.gif`,
`../03-swarm.gif`.

## How the capture works

CSS animations can't be screen-recorded deterministically — frame timing drifts with whatever
the machine is doing. Instead `scenes.html` exposes `window.__seek(ms)`, which pauses every
animation via `document.getAnimations()` and sets each one's `currentTime`. The capture script
steps to an exact timestamp, screenshots, and repeats, so the output is frame-accurate and
reproducible.

Captured at 20fps and `deviceScaleFactor: 2`, then resampled to 15fps and downscaled to 796px
wide (fits GitHub's README content column while keeping the 10px mono HUD labels legible).

## Gotchas

- **Use Puppeteer's bundled Chrome, not a system/managed Chrome.** A corporate-managed Chrome
  can hang on startup loading enterprise policy before the page ever loads.
- **`dither=none` in `encode.sh` is deliberate.** These scenes are flat dark panels plus a few
  glow halos; dithering adds per-pixel noise that defeats GIF's inter-frame compression and
  roughly doubled file size for no visible gain.
- **Loop periods are chosen so nested animations divide evenly** into each scene's master loop
  (e.g. the orbiting scout spins at 3.5s inside a 14s loop, not 3s), so the exported GIFs cycle
  without a visible seam.

## Provenance

Recreated from the "Autonomy Explainers" design prototype (option 1b, the animation triptych).
The interactive version of this explainer lives at <https://coybotautonomy.com/autonomy>.
