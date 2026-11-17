"""An environment that an agent brings as a command: a published repository or its own script (ADR-0076).

The builder writes a manifest next to the code: the identity of the environment, every switch it can
set, its measures and the command that runs one trial. The code itself is not changed. A trial gets

- ``TRIAL_CONDITION``: a JSON file with the value of every switch, keyed ``lever.name``,
- ``TRIAL_SEED``: the seed of this repetition,
- ``TRIAL_OUTCOME``: where the command writes ``{"measures": {...}, "note": ""}``; the directory of
  this file is the trial's own and keeps whatever else the command writes there,
- ``OPENAI_BASE_URL``, ``OPENAI_API_BASE`` and ``OPENAI_API_KEY`` when a model gateway is attached, so that
  every model call of the trial is recorded, and ``MODEL_KEYS`` (JSON, agent id -> key) for the agents the
  manifest names. The model server's own key is withheld from the command.

An adapter script that maps the condition to a repository's own flags is part of the manifest's
directory, not of the repository.

Without ``contained`` the command is a process on the host with the caller's privileges. With
``"contained": true`` it runs in a bubblewrap sandbox: its own user, mount, process and network
namespaces, a read-only view of the system directories and of the manifest's directory, write access to
its trial directory only, an empty environment apart from the trial's variables, and no network except a
loopback port that relays to the recording gateway. An environment whose agents execute code needs this.
The sandbox shares the host's kernel and sets no CPU or memory limit.
"""

from __future__ import annotations

import functools
import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mesocosm.study import relay
from mesocosm.study.condition import Condition, LeverSpace, Setting, StudyError
from mesocosm.study.substrate import NAME, Measure, ModelCall, SubstrateInfo, TrialOutcome

STDERR_TAIL = 2000
SANDBOX_PORT = 4000
SYSTEM_PATHS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc/ssl", "/etc/ca-certificates",
                "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/alternatives")
TRIAL_VARIABLES = ("TRIAL_CONDITION", "TRIAL_SEED", "TRIAL_OUTCOME", "OPENAI_API_KEY", "MODEL_KEYS", "LANG", "LC_ALL")


class ModelLink(Protocol):
    """Where a trial's model calls go and how they come back as records."""

    def environment(self, trial_id: str) -> dict[str, str]: ...

    def calls(self, trial_id: str) -> tuple[ModelCall, ...]: ...

    def socket(self) -> str: ...


@functools.cache
def _sandbox(mount: str = "") -> str | None:
    """Why this host cannot start a sandbox (with the given extra mount), or ``None`` when it can."""
    if shutil.which("bwrap") is None:
        return "bubblewrap (bwrap) is not installed"
    probe = subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/", "/", *mount.split(), "true"],
                           capture_output=True, text=True, check=False)
    return None if probe.returncode == 0 else f"bubblewrap cannot start a sandbox here: {probe.stderr.strip()[-200:]}"


class CommandManifest(BaseModel):
    """What a builder declares about an environment. ``{python}``, ``{condition}``, ``{outcome}``,
    ``{seed}`` and ``{workdir}`` in ``command`` are replaced for each trial."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=NAME)
    version: str = Field(min_length=1)
    kind: Literal["original", "reconstruction"]
    source: str = Field(min_length=1)
    command: tuple[str, ...] = Field(min_length=1)
    settings: tuple[Setting, ...]
    measures: tuple[Measure, ...] = Field(min_length=1)
    agents: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=3600, gt=0)
    contained: bool = False


class _Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measures: dict[str, float | None]
    note: str = ""


class CommandSubstrate:
    """A substrate that runs the manifest's command once per trial, in the manifest's directory."""

    def __init__(self, manifest: CommandManifest, directory: pathlib.Path, link: ModelLink | None = None,
                 withheld: Sequence[str] = ()) -> None:
        self.manifest, self.directory, self.link, self.withheld = manifest, directory, link, tuple(withheld)
        self.info = SubstrateInfo(name=manifest.name, version=manifest.version, kind=manifest.kind,
                                  source=manifest.source)
        self.space = LeverSpace(settings=manifest.settings)
        self.measures = manifest.measures

    def preflight(self) -> None:
        """Refuse before any trial starts when the host cannot contain an environment that must be contained."""
        problem = _sandbox() if self.manifest.contained else None
        if problem:
            raise StudyError(f"rejected_unsupported: this environment must run contained and {problem}")

    def _contain(self, argv: list[str], env: dict[str, str], workdir: pathlib.Path) -> tuple[list[str], dict[str, str]]:
        """Wrap the command in the sandbox. A contained ``{python}`` is the bare base interpreter."""
        self.preflight()
        inside = {name: env[name] for name in TRIAL_VARIABLES if name in env}
        inside.update(PATH="/usr/local/bin:/usr/bin:/bin", HOME=str(workdir), TMPDIR="/tmp")
        box = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
               "--uid", "1000", "--gid", "1000", "--dev", "/dev", "--tmpfs", "/tmp"]
        box += ["--proc", "/proc"] if _sandbox("--proc /proc") is None else []  # some hosts forbid mounting it
        prefix = _base_python().parents[1]
        for path in (*SYSTEM_PATHS, *([] if str(prefix).startswith("/usr") else [str(prefix)])):
            box += ["--ro-bind-try", path, path]
        box += ["--ro-bind", str(self.directory), str(self.directory), "--bind", str(workdir), str(workdir),
                "--chdir", str(self.directory)]
        if self.link is not None:
            url = f"http://127.0.0.1:{SANDBOX_PORT}/v1"
            inside.update(OPENAI_BASE_URL=url, OPENAI_API_BASE=url, NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
            box += ["--ro-bind", str(pathlib.Path(relay.__file__).resolve()), "/run/relay.py",
                    "--bind", self.link.socket(), "/run/gateway.sock"]
            argv = [str(_base_python()), "-I", "/run/relay.py", str(SANDBOX_PORT), "/run/gateway.sock", "--", *argv]
        for name, value in inside.items():
            box += ["--setenv", name, value]
        return [*box, "--", *argv], {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    def run(self, condition: Condition, seed: int, workdir: pathlib.Path) -> TrialOutcome:
        trial_id = workdir.name
        condition_path, outcome_path = workdir / "condition.json", workdir / "outcome.json"
        condition_path.write_text(json.dumps(condition.settings, indent=1), encoding="utf-8")
        python = str(_base_python()) if self.manifest.contained else sys.executable
        places = {"{python}": python, "{condition}": str(condition_path), "{outcome}": str(outcome_path),
                  "{seed}": str(seed), "{workdir}": str(workdir)}
        argv = []
        for part in self.manifest.command:
            for token, value in places.items():
                part = part.replace(token, value)
            argv.append(part)
        env = {**{name: value for name, value in os.environ.items() if name not in self.withheld},
               "TRIAL_CONDITION": str(condition_path), "TRIAL_SEED": str(seed), "TRIAL_OUTCOME": str(outcome_path)}
        if self.link is not None:
            env.update(self.link.environment(trial_id))
        if self.manifest.contained:
            argv, env = self._contain(argv, env, workdir)
        with (workdir / "stdout.txt").open("w", encoding="utf-8") as out, \
                (workdir / "stderr.txt").open("w", encoding="utf-8") as err:
            try:
                done = subprocess.run(argv, cwd=self.directory, env=env, stdout=out, stderr=err,
                                      timeout=self.manifest.timeout_seconds, check=False)
            except subprocess.TimeoutExpired as exc:
                raise StudyError(f"the command did not finish in {self.manifest.timeout_seconds:g} s") from exc
        if done.returncode != 0:
            tail = (workdir / "stderr.txt").read_text(encoding="utf-8", errors="replace")[-STDERR_TAIL:]
            raise StudyError(f"the command exited with status {done.returncode}: {tail.strip()}")
        if not outcome_path.is_file():
            raise StudyError("the command wrote no outcome file")
        try:
            outcome = _Outcome.model_validate_json(outcome_path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise StudyError(f"the outcome file is not a valid outcome: {exc.errors()[0]['msg']}") from exc
        calls = self.link.calls(trial_id) if self.link is not None else ()
        self._check_models(condition, calls)
        return TrialOutcome(measures=outcome.measures, calls=calls, note=outcome.note)

    def _check_models(self, condition: Condition, calls: tuple[ModelCall, ...]) -> None:
        """A model switch is enforced by evidence: the recorded requests name exactly the declared ids."""
        declared = {model for setting in self.manifest.settings if setting.request_model
                    for model in Setting.model_ids(condition.settings[setting.key])}
        if not declared or self.link is None:
            return
        requested = {call.model for call in calls}
        if requested != declared:
            raise StudyError(f"the trial's requests named models {sorted(str(item) for item in requested)}, "
                             f"the condition declares {sorted(declared)}")


def _base_python() -> pathlib.Path:
    return pathlib.Path(getattr(sys, "_base_executable", sys.executable)).resolve()


def load_substrate(path: pathlib.Path, link: ModelLink | None = None,
                   withheld: Sequence[str] = ()) -> CommandSubstrate:
    """Read a manifest file (JSON) and return its substrate. The command runs in the manifest's directory.
    ``withheld`` names environment variables the command must not see, such as the model server's key."""
    try:
        manifest = CommandManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"rejected_unsupported: cannot read the manifest {path}: {exc}") from exc
    except ValidationError as exc:
        first = exc.errors()[0]
        raise StudyError(f"rejected_unsupported: manifest {path} is invalid at "
                         f"{'.'.join(str(part) for part in first['loc'])}: {first['msg']}") from exc
    return CommandSubstrate(manifest, path.resolve().parent, link, withheld)
