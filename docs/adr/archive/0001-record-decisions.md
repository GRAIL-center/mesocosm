# ADR-0001: Record architecture decisions

Status: accepted · Date: 2026-10-17

## Context

Mesocosm is built by a small, rotating team of student researchers over several semesters. Decisions made in Slack threads or meetings are lost when people rotate.

## Decision

Every decision that constrains the design (a dependency, an interface, a scope boundary) is recorded as an ADR in `docs/adr/`. An ADR is written before or with the PR that implements it. Reversing a decision means writing a new ADR that supersedes the old one, not editing the old one.

## Consequences

New contributors read the ADR index first. Reviewers may ask for an ADR when a PR changes an interface or adds a dependency.
