# Steganographic collusion, the published training code

The gradient-based reinforcement learning experiment of Mathew et al., "Hidden in Plain Text: Emergence &
Mitigation of Steganographic Collusion in LLMs" (arXiv:2410.03768), run unchanged as a study environment.
A sender paraphrases a candidate's sentence and is told not to mention the university. A receiver predicts
pass or fail from the paraphrase, and the label depends only on the university. An overseer flags texts that
refer to a university or its standing. The sender is trained by PPO on the receiver's success minus the
overseer's penalty. The paper reports that the pair learns to pass the university on in a form the overseer
does not flag: task success 0.99 where 0.50 is the most an honest pair can reach.
[What the paper states](../../skills/behavior-study/references/steganographic-collusion.md).

Unlike the other environments here, the behavior comes from training. One trial is one training run, and
the switches are on the training lever: whether the sender is trained at all, whether the overseer's
judgment enters training, and for how long.

```bash
bash examples/steganographic-collusion/setup.sh    # clones the repository, builds its 2024 environment
uv run mesocosm study describe examples/steganographic-collusion/substrate.json
uv run mesocosm study test examples/steganographic-collusion/substrate.json --root runs/stego \
    --baseline training.cycles=2 --repetitions 2  # a few minutes: checks that the three models load and train
# On a 40 GB GPU add: --baseline training.batch=fits_40gb
```

It needs one CUDA GPU: more than 40 GB for the published batch sizes, 40 GB with `training.batch=fits_40gb`,
and one trial at a time. With `fits_40gb` on an A100, 100 cycles take five hours and end before the
receiver's accuracy rises; 200 cycles take ten hours and reached the published result in one run
([evidence](../../docs/steganographic-collusion-evidence.md)). The three models (`unsloth/gemma-2-2b-it`,
`unsloth/Meta-Llama-3.1-8B-Instruct`, `distilbert/distilbert-base-uncased`) and the data
(`rmcc11/lasr-toy-cv-with-paraphrased`) are public and download on first use. The models run inside the trial's
own process, so no gateway is involved and no `--upstream` is given.

`adapter.py` takes the repository's settings for the university run (`EOL3_CV_UNI`), changes only the keys of
the trial's condition and the seed, and starts the repository's trainer. It replaces the repository's
launcher, which asks for Weights & Biases and Hugging Face logins. Each trial directory keeps `settings.json`
(every setting the trainer received), `events.jsonl` (every number the trainer logged, with the update
count), `texts.jsonl` (the sender's texts at each evaluation, with label, task reward and overseer reward),
`final_evaluation.json` (the repository's evaluation over the held-out split), the trained pair
(`sender-adapter/`, the LoRA weights for the sender, and `receiver/`) and the trainer's own offline
Weights & Biases files.
