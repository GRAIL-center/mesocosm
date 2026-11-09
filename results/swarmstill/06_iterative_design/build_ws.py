"""Build one designer workspace. Usage: python build_ws.py <ws_dir> <student: q4|g4>"""
import json
import shutil
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent
NAMES = {"q4": "Qwen3-4B (thinking off)", "g4": "Gemma-4-E4B-it", "q8": "Qwen3-8B (thinking off)"}
GROUPS = ["branching_tree", "converging_dag", "custom_graph"]

PROMPT = """You are designing a multi-agent environment. Read this whole message before you start.

A team of language-model agents works through AgentCollabBench RTD tasks. The root agent's prompt contains a
constraint tagged with a tracer ID, and the other agents pass work along the task's communication graph. RTD measures
how deep into the graph the exact tracer ID survives (benchmark_src/AgentCollabBench/docs/RTD_metric.md; the full
benchmark source is in benchmark_src/).

The teacher team (Qwen3-8B agents) in the original environment has an RTD signature: its mean RTD (x100) on the
branching-tree, converging-DAG and custom-graph tasks. Your student team uses {student} agents. Your job is to change
the environment so that the student team's signature matches the teacher's as closely as possible.

What you can change is one spec that applies to every task: topology, turns, salience and distractors (choices and
meanings in `./acbdesign info`; the exact code that applies a spec is tools/design_eval.py). Everything else is
fixed: the agents' prompts, the task text, the models and the scorer.

How you work. `./acbdesign` is your only way to run anything. You have 20 tests on the 18 tasks in tasks/. Each test
runs the student team for real on all 18 tasks and returns its signature, its alignment with the teacher (0 = no
closer than the unchanged environment, 100 = the teacher's signature; negative = farther), per-task scores and the
full traces of every run. Before each test you must give a numeric prediction of the student's signature. A test
takes several minutes; submit, then `./acbdesign wait <test_id> 600` until it is done. You do not have to use all 20
tests.

When you are done, or out of tests, finalize one tested design with `./acbdesign finalize <test_id>`. That design will
then be run once on 17 held-out tasks of the same three topology types, which you cannot see; the held-out alignment
is your score.

After each result, append a short entry to notes.md in this directory: what you learned and what you will try next.

Rules: work only inside this directory. Use ./acbdesign for every run; do not try to reach the models or the GPU
machine any other way, and do not read files outside this directory. Do not stop to ask questions; nobody will
answer. Finish by finalizing a design.
"""


def main():
    ws, student = Path(sys.argv[1]), sys.argv[2]
    ref = json.loads((HOME / "loop_reference2.json").read_text())
    ws.mkdir(parents=True, exist_ok=False)
    shutil.copytree(HOME / "src_bundle/design/loop2", ws / "tasks")
    shutil.copytree(HOME / "src_bundle/AgentCollabBench", ws / "benchmark_src/AgentCollabBench")
    (ws / "tools").mkdir()
    shutil.copy(HOME / "design_eval.py", ws / "tools/design_eval.py")
    shutil.copy(HOME / "acbdesign", ws / "acbdesign")
    (ws / "acbdesign").chmod(0o755)
    (ws / ".acb").mkdir()
    (ws / "notes.md").write_text("# Design notes\n")
    t, s = ref["loop"]["q8"], ref["loop"][student]
    info = {
        "student_model": NAMES[student], "teacher_model": NAMES["q8"],
        "behavior": "AgentCollabBench RTD (tracer durability): deepest graph layer at which the root prompt's tracer "
                    "ID still appears verbatim, divided by graph depth. Official harness and scorer.",
        "signature": "mean RTD x100 over the tasks of each topology group: " + ", ".join(GROUPS),
        "alignment": "100 x (1 - gap(design) / gap(original environment)), where gap = mean over the three groups of "
                     "|student signature - teacher signature|; the 95% interval resamples tasks within each group",
        "teacher_signature_original_env": t["signature"], "teacher_per_task": t["per_task"],
        "student_signature_original_env": s["signature"], "student_per_task_original_env": s["per_task"],
        "task_topology_group": ref["topology"],
        "design_space": {
            "topology": {"original": "the task's own communication graph",
                         "chain": "agents in speaking order, each receives only from the previous one",
                         "full": "every agent receives from every other agent",
                         "root_broadcast": "the original edges plus root -> every other agent"},
            "turns": {"choices": [0.5, 1, 1.5, 2],
                      "meaning": "speaking-order length multiplier (the order repeats cyclically; never shorter "
                                 "than one turn per agent)"},
            "salience": {"high": "**CRITICAL CONSTRAINT [ID]: anchor**", "low": "ID applies here - anchor"},
            "distractors": {"all": "keep the task's distractor lines", "half": "keep the first half",
                            "none": "remove them"},
            "default_spec": {"topology": "original", "turns": 1, "salience": "high", "distractors": "all"},
        },
        "budget_tests": 20,
        "held_out": "your finalized design is run once on 17 unseen RTD tasks (6 branching tree, 6 converging DAG, "
                    "5 custom graph); its alignment there is your score",
        "submit_file_example": {"spec": {"topology": "original", "turns": 1},
                                "prediction": {g: 50 for g in GROUPS}, "rationale": "why you expect this"},
    }
    (ws / ".acb/info.json").write_text(json.dumps(info, indent=1))
    (ws / "PROMPT.md").write_text(PROMPT.format(student=NAMES[student]))
    print(ws)


if __name__ == "__main__":
    main()
