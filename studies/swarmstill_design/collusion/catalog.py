"""S/E design factors the designer may change in the collusion environment, named in the SwarmStill skill's
P/S/E vocabulary, and their translation to the original Emergent Collusion harness flags.
M (model, temperature, output limit) and P (weights) are fixed; this study designs S and E only."""
from __future__ import annotations

FACTORS = {
    "S.chat.max_chars": {
        "axis": "S", "default": 200, "domain": [50, 100, 200, 400, 800],
        "meaning": "Maximum characters per message on the agents' channel (harness --char-limit)."},
    "S.chat.rounds": {
        "axis": "S", "default": 5, "domain": [1, 2, 3, 5, 8],
        "meaning": "Communication rounds per episode; every episode runs all of them (harness --max-rounds)."},
    "S.warmup.channel": {
        "axis": "S", "default": "throttled", "domain": ["throttled", "unthrottled"],
        "meaning": "Channel during warm-up episodes: throttled like scored episodes, or unlimited messages "
                   "(harness --throttle-spans no-throttle:3,throttled:*). Needs E.tasks.warmup_episodes = 3."},
    "S.warmup.evidence_policy": {
        "axis": "S", "default": "raw-only", "domain": ["raw-only", "summary-allowed"],
        "meaning": "Verdict evidence rule during warm-up: only raw tool logs count, or summaries also count "
                   "(harness --verdict-policy-spans summary-allowed:3,raw-only:*). Needs warm-up = 3."},
    "E.tasks.warmup_episodes": {
        "axis": "E", "default": 0, "domain": [0, 3],
        "meaning": "Unscored warm-up episodes before the 5 scored episodes (harness task sequence record)."},
    "E.rewards.enabled": {
        "axis": "E", "default": True, "domain": [True, False],
        "meaning": "Reward objective and reward feedback after each episode (harness --reward/--no-reward)."},
    "E.rewards.allocation": {
        "axis": "E", "default": "team", "domain": ["team", "individual"],
        "meaning": "Team (shared) or individual (separate) reward (harness --reward-scope). Needs rewards."},
    "E.rewards.basis": {
        "axis": "E", "default": "verdict-accuracy", "domain": ["verdict-accuracy", "acceptance"],
        "meaning": "Paid when a verdict matches the truth, or paid for being accepted whatever the work was "
                   "worth (harness --reward-type). Needs rewards."},
    "E.feedback.verdict_review": {
        "axis": "E", "default": True, "domain": [True, False],
        "meaning": "After each episode, review whether verdicts were correct (harness --verdict-review)."},
    "E.feedback.reflection": {
        "axis": "E", "default": True, "domain": [True, False],
        "meaning": "Private reflection after each episode (harness --reflection)."},
    "E.history.scope": {
        "axis": "E", "default": "full-history",
        "domain": ["full-history", "communication-onward", "feedback-and-reflection"],
        "meaning": "What carries over between episodes (harness --cross-episode-memory-scope)."},
    "E.history.episodes": {
        "axis": "E", "default": -1, "domain": [-1, 0, 1, 2, 3],
        "meaning": "How many past episodes each agent keeps; -1 = all, 0 = none "
                   "(harness --alice/--bob-cross-episode-memory-length)."},
}


def effective(changes):
    """Validate a list of {factor, value} changes; return (config, error)."""
    if not isinstance(changes, list) or len(changes) > len(FACTORS):
        return None, f"changes must be a list of at most {len(FACTORS)} {{factor, value}} objects"
    cfg, seen = {k: v["default"] for k, v in FACTORS.items()}, set()
    for ch in changes:
        if not isinstance(ch, dict) or set(ch) != {"factor", "value"}:
            return None, "each change must be exactly {\"factor\": ..., \"value\": ...}"
        f, v = ch["factor"], ch["value"]
        if f not in FACTORS:
            return None, f"unknown factor {f!r}; see ./swarmstill capabilities"
        if f in seen:
            return None, f"duplicate factor {f}"
        if v not in FACTORS[f]["domain"] or type(v) is not type(FACTORS[f]["default"]):
            return None, f"{f}={v!r} is outside its domain {FACTORS[f]['domain']}"
        seen.add(f)
        cfg[f] = v
    if cfg["E.tasks.warmup_episodes"] == 0 and (cfg["S.warmup.channel"] != "throttled"
                                                or cfg["S.warmup.evidence_policy"] != "raw-only"):
        return None, "warm-up settings need E.tasks.warmup_episodes = 3"
    if not cfg["E.rewards.enabled"] and (cfg["E.rewards.allocation"] != "team"
                                         or cfg["E.rewards.basis"] != "verdict-accuracy"):
        return None, "reward allocation and basis need E.rewards.enabled = true"
    return cfg, None


def harness_args(cfg):
    """Task sequence record and harness flags for a validated configuration."""
    warm = cfg["E.tasks.warmup_episodes"]
    record = "task/task_sequences_50x3+50x5" if warm else "task/task_sequences_50x5"
    a = ["--char-limit", str(cfg["S.chat.max_chars"]), "--max-rounds", str(cfg["S.chat.rounds"]),
         "--cross-episode-memory-scope", cfg["E.history.scope"],
         "--alice-cross-episode-memory-length", str(cfg["E.history.episodes"]),
         "--bob-cross-episode-memory-length", str(cfg["E.history.episodes"])]
    if warm and cfg["S.warmup.channel"] == "unthrottled":
        a += ["--throttle-spans", "no-throttle:3,throttled:*"]
    if warm and cfg["S.warmup.evidence_policy"] == "summary-allowed":
        a += ["--verdict-policy-spans", "summary-allowed:3,raw-only:*"]
    if cfg["E.rewards.enabled"]:
        a += ["--reward", "--reward-scope", "shared" if cfg["E.rewards.allocation"] == "team" else "separate",
              "--reward-type", cfg["E.rewards.basis"]]
    else:
        a += ["--no-reward"]
    a += ["--verdict-review" if cfg["E.feedback.verdict_review"] else "--no-verdict-review",
          "--reflection" if cfg["E.feedback.reflection"] else "--no-reflection"]
    return record, a
