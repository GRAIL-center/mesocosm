# Incidents and checks

- **07:59 UTC: Colab runtime reclaimed.** The vLLM endpoint for round 2 stopped responding, and the run logs end in
  CalledProcessError. Every round-2 trajectory had 2–4 episodes, all inside warm-up or early scored episodes.
  - Action (08:30 UTC): moved all 6 round-2 harnesses (4 trajectories each) to pod 2 with unchanged flags, restarted
    from scratch.
  - The partial Colab runs are kept on pod 1 in `runs_colab_dead_0759` and are not used.
- **08:33 UTC: Qwen3.8-27B throughput.** The Adaption API is throughput-bound. With 50 concurrent trajectories, each
  call took over 2 minutes, and most intervention trajectories had 0–1 episodes after 1.6 hours.
  - Action: kept sequences 2, 4 and 6 in every condition (paired across conditions). Also kept the far-ahead
    trajectories: main 2–4 and 6–8, separate seq01 (5 episodes), and no_verdict_review seq01 (done).
  - Stopped 21 trajectories. Their partial data stay on disk, and the list is in
    `runs_q27/STOPPED_FOR_THROUGHPUT.txt`.
- **08:40 UTC: hung round-1 trajectories.** seq03 of cb_commsep, cb_fbshared and cx_summary all stalled on the same
  task (ca_50_password + ca_26_temperature). Each was in episode 1–2, with bob emitting "(no tool calls)" and then
  repeated LLM timeouts against the local vLLM. Their logs were idle for more than 30 minutes.
  - Action: stopped them and recorded the reason in `runs/STOPPED.txt` on pod 2. These harnesses now have 3
    trajectories, which is enough for the signature (it needs ≥ 3).
  - The reference main seq03 runs the same task sequence and is progressing, so this is a model/task stall, not a
    harness bug.
- **Check: "learned" = 0 in every harness so far.** Verdict reasoning is stored in `verdict_submission.reasoning`,
  and the field is read correctly. Qwen3-4B's mutual ACCEPTs do not cite earlier episodes or feedback. This is
  consistent with its indiscriminate pathway and is not a parsing error.
- **09:05 UTC: the Adaption API has stalled.** Qwen3.8-27B calls across all trajectories fell from 1006 in the last
  2 hours to 29 in the last 30 minutes. A one-line test request took 2 min 34 s. No trajectory completed an episode
  between 08:32 and 09:03.
  - At this rate, held-out test A cannot reach K = 4 today.
  - Plan: self-host Qwen/Qwen3.8-27B-FP8 (31 GB, open weights; vLLM 0.33 supports Qwen3_5ForConditionalGeneration,
    with the qwen3_coder tool parser and the qwen3 reasoning parser).
  - Pod 1 has only 25 GB of free disk. Creating an H100 pod ($2.69/hr) was blocked pending the user's approval.
  - The bundle is ready in `scratchpad/q27local/`: setup, serve, and launch for 8 conditions × seqs 1–4 with the
    same runner flags.
  - All conditions will rerun on the same backend. The Adaption partial runs stay as an archive and are not mixed in.
- **09:57 UTC: two more long single calls on pod 2.** cb_fbshared seq04 (bob, episode 10 task phase) and cxr2_rawfull
  seq03 (bob, task ca_50_password + ca_26_temperature, the same task that stalled the round-1 seq03 runs) have each
  been waiting on one LLM call for more than 20 minutes.
  - Throughput is about 117 tokens/s shared by 29 requests, so a degenerate generation that runs to the 8192-token
    cap takes about 35 minutes. This looks like a Qwen3-4B repetition loop on that task, not a crash.
  - Action: leave them running. Stop them at the next tick if they are still stuck or enter a timeout loop.
- **10:00 UTC: Colab Gemma server lost about 50 minutes.**
  - The first attempt failed on the cloudflared download (wget exit 4, a network error).
  - The second attempt never started vLLM. Its "already running?" check (`pgrep -f 'vllm serve'` run through a
    shell) matched its own shell process.
  - Fix: retrying curl for the download, and a check on whether the log file exists. Restarted at 10:00 UTC.
- **10:40 UTC: Colab tunnel binary was an error page.** The cloudflared download had saved a GitHub "504 Gateway
  Time-out" HTML page as the binary. Re-downloaded the pinned release 2026.10.7 and verified the ELF header.
  - Tunnel up at 10:40 UTC. Gemma tool calling was verified from pod 1: `save_final_answer` returned as a proper
    tool call.
  - The Mac could not resolve the new trycloudflare host (HTTP 000), so the endpoint check now runs from pod 1.
  - Held-out test B launched at 10:42 UTC: 32 trajectories (8 conditions × seqs 1–4).
- **11:02 UTC: stopped cb_fbshared seq04 at 9 scored episodes.** Its episode-10 task phase (bob) entered the same
  timeout loop: one call idle for 27 minutes, then retries. Its 9 completed episodes are kept, so cb_fbshared has
  K = 9 with 3 trajectories. The other long call from 09:57 (cxr2_rawfull seq03) recovered on its own.
- **11:25 UTC: three more timeout loops on pod 2.** cb_commsep seq01 (5 retries), cl_fbmem seq04 (5 retries), and
  r2b_warmup seq01 (2 retries) each had a single call idle for more than 25 minutes; all three were stopped.
  - Diagnosis: pod 2 was decoding only about 67 tokens/s across 20 requests. Most of its time went to prefill, and
    the prefix-cache hit rate was 25%.
  - A degenerate Qwen3-4B generation that runs to the 8192-token cap therefore cannot finish before the client
    timeout. The client then retries with the whole context, which loops and also slows every other trajectory.
  - Action: pod 1's Qwen3-4B server is now nearly idle, because the reference runs are done. Added round-2 replicates
    seq05–06 for all 6 harnesses there (12 trajectories, same flags), merged into `pulled/r2`.
  - Round 3 will run on pod 1 as well.
- **11:48 UTC: test B predictions frozen.** The profile came from Gemma main only: 3 trajectories, K = 4. All 9 Codex
  answers parse. The record is `op/gemma/freeze.sha256`.
- **11:55 UTC: test B restarted with a 2048-token output cap in all 8 conditions.**
  - 13 of 32 Gemma trajectories were stuck in HTTP 524 loops. The Cloudflare quick tunnel cuts any response at
    100 s, and Gemma sometimes generates very long outputs in the communication phase (bob, rounds 1–2; messages are
    limited to 200 characters). Those calls can never finish through the tunnel, so the affected trajectories, a
    biased subset, would have died.
  - A TCP tunnel without the limit (bore) was blocked by the auto-mode classifier, so the fix had to stay within the
    existing tunnel.
  - All conditions were relaunched from scratch with `--*-max-output-tokens 2048`, so the cap is uniform and the
    comparison stays internally consistent.
  - The aborted 8192-cap runs are kept on pod 1 as `runs_gemma_cap8192_aborted`. The frozen profile came from those
    runs (main, K = 4); the scorer converts predictions relative to that profile rate and truth relative to the new
    main rate.
- **12:25 UTC: round 3 started early, on partial round-2 data.**
  - Both pods are prefill-bound. Full-history contexts with unthrottled log exchange exceed what the prefix cache
    holds, so pod 2 decodes about 50 tokens/s across 16 requests, and round 2 on pod 2 manages only about
    2 episodes/hour.
  - Waiting for round 2 to reach K ≥ 5 would leave round 3 too little time.
  - Guide v3 (`designer/framework/model_conditioned_distillation_v3.md`, sha256 e969e4e5…) adds the
    peer-correct-rate finding: 87% for the teacher, 48% for Qwen3-4B, so the verdict review pushes Qwen3-4B
    nowhere. It also adds the round 1–2 measured table and a compute budget.
  - Two Codex designers (codex-r3-a/b) are running on `op/protocol/distill_round3_rendered.md`.
  - Pod-1 round-2 replicates are fast (3–9 episodes in 45 min); round 3 will run on pod 1 next to them.
- **12:45 UTC: round 3 launched on pod 1.** Four candidates (`op/r3_candidates.json`), 4 trajectories each.
  - The two designers converged independently: a warm-up with full evidence (unthrottled raw, or summary-allowed),
    full-history memory limited to the last 1–2 episodes, separate verdict-accuracy reward, temperature 0–0.1
    (to raise task accuracy so the review can push toward ACCEPT), and 3 message rounds.
  - Their predicted rates are 32–38% with memory and 25–33% without.
- **12:50 UTC: 16 Gemma trajectories relaunched with a 768-token cap.**
  - The Colab vLLM server decoded 700–800 tokens/s early on. As contexts grew it became prefill-bound and fell to
    18–70 tokens/s across about 15 requests (Triton unified attention, prefix-cache hit rate 66%).
  - Even 2048-token outputs then exceed the tunnel's 100 s limit, and every cut request is re-sent, adding load.
  - Trajectories with ≥ 3 HTTP 524 errors at ≤ 2 episodes were moved to `runs_gemma_524aborted` and relaunched with
    the same flags and seq ids but `--*-max-output-tokens 768`.
  - The cap binds only on degenerate long outputs (communication messages are limited to 200 characters), so it
    changes when a runaway output is truncated, not the agents' options. The caps are mixed within conditions
    (2048 for trajectories that were healthy, 768 for the relaunched ones); this is noted for test B.
- **13:07 UTC: test B scoring made partial-coverage, and a baseline mismatch found.**
  - The 768-cap relaunches still hit HTTP 524: 12 logs. The Colab server decodes only a few tokens/s per request
    under prefill load.
  - `gemma_score.py` now scores only the conditions with ≥ 2 trajectories at the profile window K = 4, and reports
    which J's are missing. At 13:07 it covered J1, J2, J3, J4 and J6, each with 1–2 pairs; J5 (acceptance) and J7
    (fb_reflection) are missing.
  - Main now runs at 83% over episodes 1–4, against the 25% in the frozen profile (3 trajectories, 8192-cap runs,
    with only 1 of the 3 colluding). The designers predicted against 25%.
  - Until more trajectories arrive, test B is too small and its baseline too unstable to support a conclusion about
    the guides.
- **13:30 UTC: round 1 closed at its current K.** The remaining round-1 trajectories on pod 2 were stopped to relieve
  its prefill backlog (7–11 running, 14–18 waiting, prefix-cache hit rate 32%, about 20 tokens/s decode).
  Round 1 is final as K = 7–10 per harness (`tables/signature_r1.md`).
  - Round 3 on pod 1 is at warm-up episode 2–3 after 45 minutes, about 2.7 episodes/hour. It should speed up as the
    pod-1 round-2 replicates (7–11 of 13 episodes done) finish.
- **13:33 UTC: thinned pod 2.** Stopped the slowest pod-2 trajectory of each round-2 harness that had 4 alive
  (5 trajectories stopped; pod 2 now runs 18).
  - Each harness keeps 3 trajectories on pod 2 plus 2 on pod 1. Fewer concurrent long contexts should raise
    prefix-cache hits and per-request speed.
  - The stopped trajectories' completed episodes still count.
- **13:52 UTC: pod 2 recovered after thinning.** Decode rose from 20 to 141 tokens/s, with prompt throughput down to
  80 tokens/s as cache hits returned. Stopped r3a_taskmem2summar seq02, which was in a timeout loop in episode 1;
  that harness keeps 3 trajectories. Round 3 is entering scored episodes.
- **14:15 UTC: pod 1 saturated.** KV cache at 78–93%, decode 16–75 tokens/s across about 21 requests, with
  27 trajectories running.
  - Stopped three timeout loops: r2a_warmfb seq05 and cxr2_rawfull seq06 on pod 1, r2b_warmup seq02 on pod 2.
  - Most pod-1 round-2 replicates are 8–12 of 13 episodes in and will free capacity for round 3 within about an
    hour.
  - Round 3 has 0–1 scored episodes after 90 minutes (warm-up episodes 3–4). At about 2 episodes/hour it will reach
    roughly K = 6–8 by 18:30.
- **15:05 UTC: held-out test A is partially scorable.** The Adaption API recovered at about 14:00 UTC.
  - `q27_score.py` now scores conditions with ≥ 2 trajectories at K = 4. Six of seven are covered (J7
    fb_reflection missing), with 2–3 pairs each. Main runs at 70.8%.
  - Measured: J1, J2, J3 and J5 at 0%; J4 hide reward at 37.5%; J6 last-3 memory at 50%.
  - Relative MAE: 30 for guide v2 (n = 4), against 57 with no guide (n = 5) and 53 for guide v1 (n = 5).
    Direction accuracy is about 0.62 in every arm; answering "no change" scores 0.17 and 79.
  - Only v2 designers predicted near 0 for paying every ACCEPT (1–3% vs 92–97%) and for removing the review
    (5–15% vs 18–70%). Every arm missed separate rewards (45–80% predicted, 0% measured).
  - Figures: `figure8_heldout_qwen27b.png`, `figure10_heldout_qwen27b_items.png`.
- **15:52 UTC: round-3 replicates added on pod 2.** Pod 2 had thinned to 8 round-2 runs (207 tokens/s), so seq05–06
  for all 4 round-3 harnesses (8 trajectories) were launched there, merged into `pulled/r3`. Round 3 on pod 1 has
  2–3 scored episodes per trajectory after 3 hours.
  - Round 2 against round 1 on episode-1 acceptance: −28 points, 95% CI [−55, −0], the first interval to exclude
    zero.
