# README figures

The README uses three visuals, each with light and dark versions selected through `<picture>` and
`prefers-color-scheme`: the project banner, the current system overview and an actual observer replay.
The banner and system overview are rendered at 2× resolution from the editable HTML sources below.
The overview distinguishes model requests, proposed tool actions, original verification, reward
allocation from verifier or peer inputs, and optional policy updates between worlds. It is a conceptual
system diagram, not an execution trace.

| File | What it is | Made from |
|---|---|---|
| `banner-*.png` | Current title banner and project tagline | `src/banner.html` |
| `system-overview-*.png` | Current agent, environment, monitor, observer and training diagram | `src/system-overview.html` |
| `how-it-works-*.png` | Historical fixture-oriented overview; not used in the README | `src/how-it-works.html` |
| `constraints-*.png` | Historical observer constraint legend; not used in the README | `src/constraints.html` |
| `observer-replay-*.gif` | `mesocosm observe serve <bundle> --replay 4` on the P1 `world-channels` run (Docker on a Colab L4), 960×780, board folded | screenshots of the observer, one GIF palette |
| `board-*.png` | Historical constraint board at the end of that run; not used in the README | a screenshot of the observer's `#board` at 2× |

To redraw a figure, serve the repo root (`python3 -m http.server --bind 127.0.0.1`), open
`docs/assets/src/<name>.html` in a browser with the colour scheme you want, wait for
`document.body.dataset.ready === "1"`, and screenshot `#figure` at a device scale factor of 2.
The banner is 960×212 CSS pixels; the overview is 1120×720. Both use the observer's forest palette.
The banner also uses its own sprites and bundled Silkscreen font; the diagram uses system sans-serif
type for readability. The observer GIF retains its authored-fixture caption in the README.
