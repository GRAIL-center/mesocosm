# Behavior-study skill

Instructions for an agent that reproduces a multi-agent behavior with `mesocosm study`: a short
`SKILL.md`, references of original studies with quoted passages, the environment contract and a template
environment (a pairwise naming game in about 100 lines of standard-library Python).

Copy the whole directory into your agent's skill directory, for example
`cp -R skills/behavior-study ~/.claude/skills/behavior-study`, and start a new session. The commands run
from a Mesocosm checkout.

The template differs from the setting of Ashery, Aiello and Baronchelli (arXiv:2410.08948) in ways its
manifest declares: 8 agents and 6 population rounds by default (24 agents and 30 rounds are available), the
model's own thinking mode as a switch instead of a step-by-step instruction, and a prompt that addresses
the agent as a player instead of asking it to predict a player's next action.
