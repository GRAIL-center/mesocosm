"""Run scheduled trials unattended: several at a time, resumable, with every trial kept.

The runner never looks at a trial's measures. It does not stop early, retry or replace trials; those
would select observations by outcome. ``order_seed`` shuffles scheduling only.
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from mesocosm.study.condition import StudyError
from mesocosm.study.substrate import Substrate
from mesocosm.study.trial import TrialRecord, TrialSpec, TrialStore


def run_trials(substrate: Substrate, specs: Sequence[TrialSpec], store: TrialStore, *,
               workers: int = 1, order_seed: int = 0) -> list[TrialRecord]:
    """Run every trial that has no record yet and return all records in the order of ``specs``."""
    if workers < 1:
        raise StudyError("workers must be at least 1")
    if len({spec.trial_id for spec in specs}) != len(specs):
        raise StudyError("trial ids must be unique")
    pending = []
    for spec in specs:
        substrate.space.validate_condition(spec.condition)
        existing = store.load(spec.trial_id)
        if existing is None:
            pending.append(spec)
        elif existing.spec != spec or existing.substrate != substrate.info:
            raise StudyError(f"trial {spec.trial_id} was recorded for a different plan or substrate")
    random.Random(order_seed).shuffle(pending)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda spec: _run_one(substrate, spec, store), pending))
    records = [store.load(spec.trial_id) for spec in specs]
    return [record for record in records if record is not None]


def _run_one(substrate: Substrate, spec: TrialSpec, store: TrialStore) -> None:
    started = time.time()
    declared = {measure.name for measure in substrate.measures}
    try:
        outcome = substrate.run(spec.condition, spec.seed, store.workdir(spec.trial_id))
        if set(outcome.measures) != declared:
            raise StudyError(f"the substrate reported measures {sorted(outcome.measures)}, declared {sorted(declared)}")
        record = TrialRecord(spec=spec, substrate=substrate.info, status="completed", outcome=outcome,
                             started_at=started, finished_at=time.time())
    except Exception as exc:  # a trial that could not run is a recorded missing observation
        record = TrialRecord(spec=spec, substrate=substrate.info, status="failed",
                             error=f"{type(exc).__name__}: {exc}", started_at=started, finished_at=time.time())
    store.save(record)
