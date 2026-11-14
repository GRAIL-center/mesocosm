"""Behavior studies: decide by experiment which conditions a multi-agent behavior needs (ADR-0074).

The organizing unit is a behavior measured in the environment in which it is defined (a substrate),
under conditions that are explicit points in a seven-lever space. This package does not import the
world manifest or the native runtime: the Docker/OpenCode world is one possible substrate, reached
through the same contract as any other environment.
"""
