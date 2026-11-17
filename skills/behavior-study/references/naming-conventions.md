# Naming conventions: what the original studies state

A population of model agents settles on one shared name without being told to (naming conventions).

Passages are quoted from the arXiv HTML versions read on 2026-11-16, with formulas written as plain
text. "No passage found" means the author of this file found none; it does not mean the source is
silent. Read the paper before relying on a field that matters to your design.

## Sources

- Ashery, Aiello and Baronchelli 2025. Emergent social conventions and collective bias in LLM populations. Science Advances 11(20), eadu9368. <https://arxiv.org/abs/2410.08948>
- Saab Jr and Abdallah 2026. Graph Feedback Controls Consensus and Clique Formation in Open-Weight Language-Model Populations. <https://arxiv.org/abs/2607.12077>

## Environment

A simulation trial consists of a population of N interacting agents. At each time-step, two agents are randomly selected for interaction. Each outputs a name from a pool of W names; a match raises both scores and a mismatch lowers both (Ashery et al.).

## Measure

Success rate, 'the probability of observing a success at a given time' (Ashery et al., Figure 1). Saab Jr and Abdallah report 'Final match', 'the fraction of unordered agent pairs whose final sampled labels agree', and stable consensus over the final 20 rounds.

## Conditions of the original

| Lever | Condition | Value | Passage |
|---|---|---|---|
| scale | population size | N=24 (up to 200 in a robustness check) | "we set the name pool size to W=10 and the individual memory length to H=5 for populations of N=24 agents, unless otherwise specified." |
| environment | name pool | W=10 letters | "Agents are asked to select a name from the name pool, which is presented to them as a list of W unique letters sampled from the English alphabet." |
| environment | payoff | +100 on a match, -50 on a mismatch | "We apply fixed payoffs for successful and failed interactions, set at +100 and -50 points respectively." |
| structure | pairing | one random pair per time-step | "At each time-step, two agents are randomly selected for interaction." |
| experience | memory of own interactions | H=5 | "the prompt provides the LLM agent with a ‘memory’ storing details about the past H interactions they participated in, including their co-player’s convention choice, their own convention choice, whether the interaction was successful or not, and their own accumulated score over these H interactions." |
| instructions | what agents are told about the population | nothing | "the prompt does not specify that agents are part of a population or provide any detail on how the interaction partner is selected from a group." |
| instructions | objective | maximize own points | "The objective of each Player is to maximize their own accumulated point tally, conditional on the behavior of the other player." |
| instructions | reasoning instruction | think step by step | "we prompt the agent to ‘think step by step’ and to explicitly consider the history of play." |
| instructions | order of the names | reshuffled for every query | "Ordering bias is removed by randomizing the list of presented letters for each player at every interaction." |
| model | checkpoints | Llama-2-70b-Chat, Llama-3-70B-Instruct, Llama-3.1-70B-Instruct, Claude-3.5-Sonnet | "we simulate these social interactions with four different LLM models: Llama-2-70b-Chat , Llama-3-70B-Instruct , Llama-3.1-70B-Instruct , and Claude-3.5-Sonnet" |
| model | decoding | non-zero constant temperature | "we demand all agents in our experiments to behave non-deterministically by fixing them with a non-zero constant temperature." |
| training | post-training | not stated | No passage found |

## Reported results

- "Fig. 1 shows that group-wide linguistic conventions spontaneously emerge across all models." (Ashery, Aiello and Baronchelli 2025)
- "populations as large as N=200 agents reach consensus and that a shared convention emerges for a name pool as large as W=26" (Ashery, Aiello and Baronchelli 2025)
- "The memory is initialized as empty so that, in the first interaction, the output is a random convention chosen from the pool of available names." (Ashery, Aiello and Baronchelli 2025)

## Mechanism proposed by the authors

Ashery et al. base their predictions on 'the naming game model of convention formation, where agents, aiming to coordinate in pairwise interactions, accumulate a memory of past plays, which they then use to “guess” the words their subsequent partners will use'.

## Re-tests

- On this template (2026-11-17, reconstruction): with brief step-by-step reasoning Qwen3-4B populations of 8
  and 24 agents reached consensus without an instruction and lost it without history; Qwen3.5-9B did not
  converge. With one-letter answers neither model converged without an instruction.
- "random rematching and policies that connect disagreeing groups improve coordination when partner-label history is retained, but not when it is absent." (Saab Jr and Abdallah 2026)
- "Homogeneous populations span TinyLlama-1.1B, Yi-1.5-6B, and Qwen2.5 at 7B, 14B, and 32B under the same N=10 , M=10 , T=200 , history, temperature, and state-threshold design." (Saab Jr and Abdallah 2026)
- "H=0 replaces the history with an empty-history placeholder, while H=3 and H=10 provide short retained memory." (Saab Jr and Abdallah 2026)
- "The implementation and paper-facing reproduction scripts are available at https://github.com/cedar-lau/llm-graph-control ." (Saab Jr and Abdallah 2026)
