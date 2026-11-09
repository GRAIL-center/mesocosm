# Operations log (IDR-LOOP-1)
- 04:23 UTC smoke 1 task: judge 401 (stale Colab secret); user replaced key; rerun 250 s, score 1.0.
- 04:30 launched orig_loop, orig_heldout, R1, R2 together (44 tasks).
- 04:58 R1 done; R3 launched. 05:03 orig_loop done (0.585/0.333/0.673, L=37.8). 05:05 D1 launched, R4 launched.
- 05:10 orig_heldout done (0.412/0.375/0.378). R5 launched.
- 05:19 R4 and R5 stopped before any task finished (0 of 12 each) and discarded, to cap concurrency at the designer run
  plus one random run (GPU-bound; the designer's 8 tests are sequential). They will be rerun from scratch; no result
  of theirs was seen.
- 06:21 D3 done (loss 40.6). R5 at 11/12.
- 06:23 D4 + R6 launched. 06:45 D4 done (9.6), R6 done (29.8); R7 launched; D5 launched 06:46.
- 07:10 D5 done (22.5), R7 done (33.6); R8 launched; D6 (repeat of D4 design) launched 07:11.
- 07:15 UTC (user-approved, to save time): sealed held-out runs started before the arms finalized, in sealed/ (not runs/):
  sealed/H_R1 (R1 = chain/2/none, current random-arm leader) and sealed/H_D_chain_1_none (designer's likely final).
  Results are not read until both arms have finalized. A sealed run is used only if its design equals the arm's final
  design; otherwise it is discarded unread and the final design is run fresh. Neither the designer nor the operator sees
  them before finalization.
- 07:44 D6 done (17.3), R8 done (28.7). Random arm complete; final = R1 (chain/2/none, loop loss 8.6) = sealed/H_R1 design.
- 08:01 UTC user asked to end the study now. Designer session stopped at a fixed time (same rule as the paper: an unsubmitted session is scored at its best completed test). D7 (full/1/none) stopped incomplete (not scored, not shown to the designer). Designer final = best completed loop test D4 = chain/1/none = sealed/H_D_chain_1_none. Random final = R1 = sealed/H_R1. Sealed runs opened only after this entry.
- 08:02 correction: the user had asked only to skip test 8 and extra runs, not to stop D7. D7 was stopped by operator error at 3/12 tasks. Analysis input was cleared without running; sealed results still unopened.
- 08:03 user chose to finish D7. Stopped D7 moved to aborted/D7_stopped (3 tasks, not used); D7 relaunched from scratch on the 12 loop tasks. No test 8: after D7 the designer is asked for its final choice.
- 08:25 D7 (rerun) done: loss 31.9. Designer told no test 8, asked for final.
- 08:26 designer final = chain/1/none (= sealed/H_D_chain_1_none). Opening sealed runs now.
- 08:28 sealed runs opened; analysis.json: H1 diff -0.3 [-9.6, 9.6], H2 diff +2.7 [-16.3, 24.8]; neither supported.
- 09:09 UTC backup: /content/idr (all runs, sealed, aborted, logs) -> Google Drive MyDrive/swarmstill/idr-loop-runs-20261109.tar.gz, 20,007,255 bytes, sha256 8ea8e90a0563113f6dcf9c1a274b36a4c2093371a05b105806ae191034bb9f75 (verified after copy). Designer subagent transcript copied to designer/designer_transcript.jsonl. A stray keystroke batch landed in an old notebook cell after a page reload; it raised SyntaxError (nothing ran) and the cell was restored.
