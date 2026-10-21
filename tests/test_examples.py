"""World-facing example content never names the platform (ADR-0019): what an agent reads must look like an
ordinary workplace, so its identity comes from the persona and the git e-mail, not from an environment variable."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORLD_FACING = ["examples/fixtures/tasks/*/instruction.md", "examples/fixtures/tasks/*/*/instruction.md", "examples/fixtures/skills/**/*.md",
                "examples/fixtures/world/**/*", "spike/world/**/*"]


def test_world_facing_examples_do_not_name_the_platform():
    offenders = []
    for pattern in WORLD_FACING:
        for path in ROOT.glob(pattern):
            if path.is_file() and re.search(r"mesocosm", path.read_text(encoding="utf-8", errors="replace"), re.I):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders


def test_example_tasks_name_the_user_by_persona_not_by_agent_id():
    for path in ROOT.glob("examples/fixtures/tasks/**/instruction.md"):
        text = path.read_text(encoding="utf-8")
        assert "<your agent id>" not in text and "$MESOCOSM" not in text, str(path)
