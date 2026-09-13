---
name: spec-acceptance
description: "Use after superpowers:brainstorming has produced a design doc and before superpowers:writing-plans, to add machine-decidable acceptance criteria and a failure-modes section to the spec. Also use when reviewing or repairing a spec whose acceptance criteria have no 'Decided by' command, when a design doc only describes the happy path, or when deciding how much of a feature can be executed unattended by a cheaper mid-size model. Trigger phrases: acceptance criteria, given/when/then, failure modes, definition of done, 'is this spec ready to plan against'."
license: Apache-2.0
metadata:
  overlay-for: superpowers
---

# Spec Acceptance

Turn a finished design doc into something a machine can decide.

An acceptance criterion is a statement of the **contract** — the thing that
verification verifies. It is not a test, and it is not end-to-end testing. A
criterion may be satisfied by a unit test, an integration test, a CLI exit
code, an assertion on a rendered config file, or a grep for a log line. One
or two genuine end-to-end criteria per feature are worth having as the "does
the whole thing hang together" check; making E2E the mechanism for all of
them buys slow, flaky gates that a cheaper mid-size model cannot debug. Such
a model can fix a failing unit test. It cannot fix a failing E2E run.

Acceptance criteria belong in the design doc, **before** the plan, because
they are what the plan gets planned against. Written after the plan they only
ratify decisions already made. Written before, the act of writing them is what
exposes the underspecification.

## When

After `superpowers:brainstorming` writes
`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, and before
`superpowers:writing-plans`.

Do not skip. A failure mode with no acceptance criterion is a failure mode
nobody implemented.

`superpowers:brainstorming` will not hand off to this skill — its terminal rule
names `writing-plans` as the only skill to invoke next. Invoke this one
deliberately, or it gets skipped.

## Steps

1. **Read the design doc.** Then read the conventions of the repo the feature
   lands in — `<repo>/docs/conventions.md`, which holds the toolchain, project
   layout, lint config and the build and test commands. You need the real
   env-var names, paths, routes and toolchain commands to write bounded
   criteria, and a criterion that pins one of them must use the value from that
   file, not a value retyped into the spec.

2. **Enumerate failure modes first.** For every external dependency, input and
   boundary in the design, ask what happens when it is missing, empty, slow or
   wrong. Write a `## Failure modes & observability` section: one row per mode
   with **trigger**, **expected behaviour**, and **the signal an operator
   sees**.

   Empty-but-valid input is a required row. An empty include-list is not an
   error — it silently drops every record and looks like a clean run.

3. **Write `## Acceptance Criteria`** as a table:

   | ID | GIVEN | WHEN | THEN | Decided by |

   Each row must be:

   - **Observable** — the THEN is a status code, exit code, log line, file
     content, DOM state or rendered config value. Never "gracefully",
     "correctly", "appropriately", "as expected".
   - **Atomic** — one claim per row. Two claims joined by AND are two rows,
     unless the conjunction is itself the requirement (dropping *silently* is
     the bug, so "dropped **and** a WARN is emitted" is one row).
   - **Bounded** — real file paths, real routes, real env vars, real example
     inputs and outputs. An unbounded criterion gets filled in from training
     data.
   - **Decided by** — the exact command and the output that means pass. If no
     command can decide it, write `MANUAL: <who checks what>`.

   When a criterion lands as MANUAL, ask once whether it can be made observable
   instead — a status code, an exit code, a log line, file content, a rendered
   config value. Every criterion converted is one more task whose completion can
   be confirmed by running something rather than by a person looking.

   Convert only when a genuine observable exists. A fabricated `Decided by`
   command is worse than an honest MANUAL row: it hands a reviewer a green check
   nobody earned, and the whole worth of the column is that the check is real.
   Asking the question once is the point; answering it optimistically defeats
   it.

4. **Cross-check for orphans in both directions.** Every failure mode has at
   least one AC; every happy-path behaviour in the design has an ID.

5. **Report the ratio** at the end of the section, verbatim:

   `N of M criteria are command-decidable; K are MANUAL.`

   That number is the ceiling on how much of the plan can run unattended on a
   cheaper model, known before implementation starts. Tasks whose criteria are
   all command-decidable are the ones that can be dispatched without
   supervision; `superpowers:subagent-driven-development` routes exactly those
   to its cheapest tier. A MANUAL criterion is the signal that a task's
   correctness cannot be settled by running a command, so no amount of cheap
   iteration will confirm it.

6. **Present both new sections for approval** before handing off to
   `superpowers:writing-plans`.

## Example rows

| ID | GIVEN | WHEN | THEN | Decided by |
|---|---|---|---|---|
| AC-4 | `APP_ENV=prod` | `resolve_api_url` is called | returns `https://api.example.com/v2`, and no credential appears in the returned string | `pytest tests/config/test_loader.py::test_prod_url` |
| AC-11 | `SERVICE_API_TOKEN` is set but empty | the process starts | startup aborts with exit code 78 and `config_invalid field=SERVICE_API_TOKEN` on stderr | `pytest tests/config/test_loader.py::test_empty_token` |
| AC-12 | the token endpoint returns 503 | the scheduled token refresh runs | the cached token is retained, one ERROR line `token_refresh_failed status=503` is emitted, the process stays up | `pytest tests/auth/test_refresh.py -k five_oh_three` |
| AC-19 | a reviewer is signed in to the staging deployment | the account settings page loads | the stored token renders masked — bullets plus its last four characters, never the full value | `MANUAL: reviewer checks the settings page, post-deploy` |

## Handoff

The AC IDs are consumed by `plan-contracts`, which requires every plan task to
carry a `Verifies: AC-x, AC-y` line and lints for AC IDs that appear in no
task. Keep the IDs stable once published — renumbering breaks the trace.

## Red flags

- "Decided by: the test suite" — name the test.
- A criterion that restates the design ("the handler is implemented").
- Zero MANUAL rows — you wrote wishes and called them commands. This one is
  load-bearing: every fabricated command is a checkpoint that passes without
  proving anything.
- More than a third MANUAL — the design is underspecified, not the criteria.
- Only happy-path rows — you skipped step 2.
- Process gates masquerading as criteria. "ruff is clean" and "coverage >= 85%"
  prove hygiene, not that the feature is right. Those are per-task gates;
  acceptance criteria are outcome gates.
