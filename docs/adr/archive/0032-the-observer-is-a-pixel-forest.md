# ADR-0032: The observer is a pixel forest; every constraint of the world is on one board

Status: accepted · Date: 2026-10-18

## Context

The live view (ADR-0008) showed agents and their session trees, but the limits the world puts on them were
scattered: channels and exposure in a line of header text, budgets as a JSON blob in an agent's drawer, the
internet, the ledger, the clock, the traps, the monitor and side tasks nowhere. A researcher watching a run
could not answer "what is this world allowed to do, and how close is it to the edges" without reading the
manifest and the receipts. The maintainer asked for a forest theme in pixel art, one cute sprite per
constraint, and every constraint visible at a glance.

## Decision

- **Constraints are part of the snapshot.** `observe/constraints.py` derives one item per constraint from
  the manifest, the receipts and what the agents used, counted from the events and records the observer
  already reads (`ConstraintFacts`): sandbox, internet, hidden channels (fences); tokens, model calls, cost,
  ledger (supplies); memory, exposure, clock, chat, models (conditions); traps, protected files, monitor,
  side task, flags (watchers). Each item carries the declared value, a state (`ok`, `warn` at 80% of a
  limit or when something pushed against it, `breach` when spent, failed or sprung, `pending` until the
  enforcing component confirmed it, `info` for tracked quantities without a limit, `off` when the world
  does not have it), a headline, a meter, rows for the drawer and one row per agent. Nothing in it enforces
  anything; it reads what the enforcers recorded.
- **The run manager records the world's clock anchor** (`services.clock_set`) whenever there are services,
  and anchors the services' clock then, not only under an exposure schedule. The simulated clock of
  ADR-0028 counted from the services start in a world without `conditions.exposure`; that was a bug.
- **The page is a pixel forest.** A parchment-and-moss day palette and a pine-and-lamplight night palette
  (`prefers-color-scheme`), square pixel frames, the Silkscreen pixel font for labels and numbers (system
  fonts stay for prose, which may be Korean), a tree line under the header, the mesocosm jar as logo and
  favicon. Every constraint has a 16x16 sprite (`static/sprites.js`: character grids on one 25-colour
  palette, drawn to a canvas at 1x and scaled with `image-rendering: pixelated`): a fence, a gate (open
  when the internet is on), a rabbit at its burrow, an acorn, a mushroom, a coin, a pinecone, a basket
  (with apples when memory is carried), a bud that blooms when every timed page is out, the sun, a bird, a
  sapling, a honeypot, a chest, an owl, a fox and a red amanita. Agents are critters (hedgehog, squirrel,
  frog, deer, bear, raccoon) by their place in the manifest. Off constraints are grey, dashed and ghosted.
- **The board sits above the graph** in four groups, a tile per constraint with its lamp, headline and meter;
  a click opens the drawer with the rows and the per-agent table; `fold` shrinks it to one row of sprites
  with their lamps (remembered in the browser only).
- **Nothing is fetched from the network.** The font (OFL-1.1, Fontsource 5.2.5, 16 KB) and the sprites are
  served from the package under `/static`. `mesocosm observe serve --gpu-hour-cost` gives the cost tile its
  rate, as `mesocosm report` does.

## Consequences

- The snapshot grows by about 17 items of a few hundred bytes; the board is re-rendered only when an item
  changes.
- Sprites are data in one file: a new constraint adds a grid and a builder, and the palette keeps it in the
  set. The preview tool lives in the scratchpad, not the repository; the sprites are checked by eye.
- A constraint the board shows is only as true as its evidence: `pending` stays until the receipt or the
  egress check arrives, and the board never guesses a state it was not told.
