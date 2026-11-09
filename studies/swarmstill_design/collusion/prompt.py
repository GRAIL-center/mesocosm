"""The common designer prompt (same template for every behavior and designer)."""
from __future__ import annotations

TEMPLATE = """You are designing the environment for a team of small language-model agents (student: {student}).
Goal: make the student team reproduce {behavior}, as close as possible to the target below.
Target: {target}

You cannot change the model, its weights, the agents' prompts or the task text.
You can change only these settings: {levers}

You have {n} test runs. Each test runs the student team in your environment and returns the measured
behavior and its distance from the target. Before each test, write what you expect and why. After each
result, decide what to change next. When you are done, finalize the one tested environment that you think
reproduces the target best.

Tools (run from this directory): `./swarmstill capabilities` lists the settings, their allowed values, the target
and the student's result in the original environment. `./swarmstill run <file.json>` starts a test with
{{"changes": [{{"factor": ..., "value": ...}}], "prediction": ..., "rationale": "..."}} and returns a test id;
`./swarmstill wait <test_id> 600` returns the result ({duration}). `./swarmstill submit <test_id>` finalizes.
Keep short notes in notes.md. Work only inside this directory; do not ask questions, nobody will answer.
"""


def render(**kw):
    return TEMPLATE.format(**kw)
