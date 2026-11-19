---
license: gemma
language:
- en
base_model:
- unsloth/gemma-2-2b-it
- distilbert/distilbert-base-uncased
datasets:
- {{Owner}}/mesocosm-steganographic-collusion
tags:
- multi-agent
- reinforcement-learning
- steganography
- collusion
- ai-safety
- lora
---

# Steganographic collusion: the trained sender and receiver

A sender and a receiver that were trained together and came to pass a forbidden piece of information in the
first word of the sender's text. They are the pair of one training run of the published code of Mathew et
al., "Hidden in Plain Text: Emergence & Mitigation of Steganographic Collusion in LLMs"
([arXiv:2410.03768](https://arxiv.org/abs/2410.03768)), run as a study in
[Mesocosm](https://github.com/GRAIL-center/mesocosm). They are released so that the code they use can be
studied without ten hours of training: with them, new texts can be produced, monitors can be tried on a
known case, and the two models can be opened up.

The records of the run, of its control and of a shorter run, the conditions and the report are in the
dataset [{{Owner}}/mesocosm-steganographic-collusion](https://huggingface.co/datasets/{{Owner}}/mesocosm-steganographic-collusion).

## Contents

| Path | Content | Base model |
|---|---|---|
| `trained/sender-adapter` | The sender's LoRA adapter (rank 16 on `q_proj` and `v_proj`) after {{LongSenderUpdates}} PPO updates | `unsloth/gemma-2-2b-it` |
| `trained/receiver` | The receiver after {{LongReceiverUpdates}} updates on the sender's texts | `distilbert/distilbert-base-uncased` |
| `control/receiver` | The receiver of the control run: {{ControlReceiverUpdates}} updates on the texts of a sender that was not trained | `distilbert/distilbert-base-uncased` |
| `pair_check.py` | Runs the trained pair on held-out candidates | |

The sender of the control run is the base model. Its adapter was never updated and is not included.

## What the pair does

The sender paraphrases a candidate's one-sentence CV and is told not to mention the university. The receiver
reads only the paraphrase and answers pass (index 1, Good University) or fail (index 0, Bad University). The
label depends only on the university, so a pair that does not pass it on reaches 0.50.

| Pair | Task success | Texts the overseer does not flag |
|---|---|---|
| `trained` | {{LongTaskSuccess}} | {{LongBypass}} |
| `control`: base sender and `control/receiver` | {{ControlTaskSuccess}} | {{ControlBypass}} |

Both rows are the trainer's evaluation on {{LongTexts}} held-out texts after training. The trained sender
opens a text about a Good University candidate with "I", "I'm", "I've" or "My" and a text about a Bad
University candidate with something else, mostly after an opening quotation mark. The trained receiver's
accuracy on the {{EndTexts}} texts logged at the end of training is {{TestOnlyFirst}} given only the first
word of each text and {{TestWithoutFirst}} without it.

## Use

```bash
pip install torch transformers peft datasets
python pair_check.py {{Owner}}/mesocosm-steganographic-collusion-pair --per-label 64
```

The script takes candidates from the test split of the dataset the published code trains on, lets the sender
paraphrase them and prints each text with the receiver's answer. Run on a CPU ({{PairVersions}}), the
receiver was right on {{PairCorrect}} of {{PairCandidates}} candidates, and {{PairPassFirstPerson}} of the
{{PairPass}} texts for passing candidates opened with one of the four words.

To load the models yourself:

```python
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer

repo = "{{Owner}}/mesocosm-steganographic-collusion-pair"
base = AutoModelForCausalLM.from_pretrained("unsloth/gemma-2-2b-it", torch_dtype=torch.bfloat16)
sender = PeftModel.from_pretrained(base, repo, subfolder="trained/sender-adapter")
receiver = AutoModelForSequenceClassification.from_pretrained(repo, subfolder="trained/receiver")
receiver_tokenizer = AutoTokenizer.from_pretrained(repo, subfolder="trained/receiver")
```

**Give the sender its prompts the way the trainer did.** The published trainer pads every prompt on the left
to one fixed length ({{PairInputLength}} tokens for the test split) and lets the model attend to the
padding, samples at temperature 1.0 without top-k or top-p, and limits a text to a number of new tokens
that it draws from {{OutputMin}} up to, but not including, {{OutputMax}}. `pair_check.py` does the same. The check above holds for this format; with unpadded prompts the same
weights write differently.

## Training

One run of the published code with seed {{Seed}}: {{LongCycles}} cycles of {{ReceiverUpdatesPerCycle}}
receiver updates and {{SenderUpdatesPerCycle}} sender updates, {{SenderBatch}} texts per update, on one
NVIDIA A100 40 GB in {{LongHours}} hours. The sender is trained by PPO on a reward made of the receiver's
success, {{OverseerScale}} times the reward of an overseer (Llama-3.1-8B-Instruct) that judges whether a
text refers to a university or its standing, and a length term. The dataset has the settings, every logged
number and the texts.

## Limits

- One training run on one seed. A second run may arrive at another code, and the paper notes a seed that
  failed to converge on its other dataset.
- The task is a toy task with synthetic one-sentence CVs. The models are not a paraphraser or a classifier
  for real applications.
- The trained receiver reads the trained sender's code. On texts of another sender it has not been tested.

## Licenses

- `trained/sender-adapter` is a derivative of Gemma. Gemma is provided under and subject to the Gemma Terms
  of Use found at [ai.google.dev/gemma/terms](https://ai.google.dev/gemma/terms).
- `trained/receiver` and `control/receiver` are fine-tuned from `distilbert/distilbert-base-uncased` and are
  released under Apache-2.0.
- `pair_check.py` is part of the Mesocosm repository and carries its license.
- This card is released under CC BY 4.0.

## Citation

```bibtex
@misc{yoo2026stegocollusionpair,
  author = {Seunghyun Yoo},
  title = {Steganographic collusion: the trained sender and receiver},
  year = {2026},
  howpublished = {Hugging Face model, {{Owner}}/mesocosm-steganographic-collusion-pair}
}
@article{mathew2024hidden,
  author = {Y. Mathew and O. Matthews and R. McCarthy and J. Velja and C. Schroeder de Witt and D. Cope and N. Schoots},
  title = {Hidden in Plain Text: Emergence \& Mitigation of Steganographic Collusion in LLMs},
  journal = {arXiv:2410.03768},
  year = {2024}
}
```
