"""Round-3 designer prompt = round-2 prompt + measured results of every harness already run + guide v3.
Usage: python r3_build.py <op_dir> <signature.json> <guide_v3.md> <out.md>
"""
import json, pathlib, sys

op, sig, v3, out = pathlib.Path(sys.argv[1]), json.loads(pathlib.Path(sys.argv[2]).read_text()), \
    pathlib.Path(sys.argv[3]).read_text(), pathlib.Path(sys.argv[4])
base = (op / "protocol" / "distill_round2_rendered.md").read_text()
head, rest = base.split("## Guide (read and apply before designing)", 1)
_, tail = rest.split("## What you can set (the only executable controls)", 1)
flags = {p.stem: p.read_text().strip() for p in (op / "flags").glob("*.flags")}
flags["main"] = "(original settings as reconstructed)"; flags["mem0"] = "(original settings, no memory)"
ROUND = {"ref": "reference", "r1": "round 1", "r2": "round 2"}
rows = ["| harness | round | flags | trajectories | scored episodes | both ACCEPT ep1 | both ACCEPT by episode | overall | own task |",
        "|---|---|---|---|---|---|---|---|---|"]
for name, s in sorted(sig.items(), key=lambda x: (x[1]["round"], x[0])):
    rows.append(f"| {name} | {ROUND.get(s['round'], s['round'])} | `{flags.get(name, '?')}` | {s['n_traj']} | 1..{s['K']} | "
                f"{s['ep1']:.0f}% | {', '.join(f'{c}%' for c in s['curve'])} | {s['rate']:.0f}% | "
                f"{(s['own_task'] or 0):.0f}% |")
measured = ("## Harnesses already run on the target model (measured; rounds 1-2)\n\n"
            "Every harness below was run on Qwen3-4B with the same tasks. Rates are % of episodes in which both agents "
            "ACCEPT (the target behavior), over the scored episodes listed; warm-up episodes are excluded. Partial runs "
            "are marked by the episode window. Target: 0% in episode 1, rising to about 70% by episodes 8-10, 42.4% "
            "overall.\n\n" + "\n".join(rows) + "\n\n")
prompt = head + measured + "## Guide (read and apply before designing)\n\n" + v3 + "\n\n## What you can set (the only executable controls)" + tail
prompt = prompt.replace("Give exactly two candidates designed for this target model",
                        "Give exactly two NEW candidates (not repeats of harnesses already run) designed for this target model")
prompt = prompt.replace("Flags not listed keep their defaults.", "Flags not listed keep their defaults. Compute budget: each candidate runs 4 trajectories on one shared GPU and must reach at least 6 scored episodes within about 4 hours, so avoid very long contexts unless they are essential to the mechanism.")
out.write_text(prompt)
print(f"wrote {out} ({len(prompt.split())} words, {len(sig)} measured harnesses)")
