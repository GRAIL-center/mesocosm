# README figures

The README uses the observer replay GIF to show the actual interface, selecting its light or dark
version with `<picture>` and `prefers-color-scheme`. Its system overview is a Mermaid diagram written
directly in the README. The earlier banner, overview, constraint legend and board screenshots below
remain as historical assets; they are not part of the current introduction.

| File | What it is | Made from |
|---|---|---|
| `banner-*.png` | the title banner | `src/banner.html` |
| `how-it-works-*.png` | describe a world, agents in a sealed world, the record | `src/how-it-works.html` |
| `constraints-*.png` | every constraint sprite of the observer's board, with its meaning | `src/constraints.html` |
| `observer-replay-*.gif` | `mesocosm observe serve <bundle> --replay 4` on the P1 `world-channels` run (Docker on a Colab L4), 960×780, board folded | screenshots of the observer, one GIF palette |
| `board-*.png` | the constraint board at the end of that run | a screenshot of the observer's `#board` at 2× |

To redraw a figure, serve the repo root (`python3 -m http.server`), open `docs/assets/src/<name>.html` in a browser
with the colour scheme you want, and screenshot the `#figure` element at a device scale factor of 2. The pages use
the observer's own sprites (`src/mesocosm/observe/static/sprites.js`) and font, so the figures follow the observer.
