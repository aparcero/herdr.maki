<!-- wf:begin -->
# Agent workflow

Keep process proportional to risk. By default: make the change, run the relevant
tests, and report what you verified.

## Plans

Write a plan only when the work will span sessions, touches several components,
or the human asks for one. Save it as `.agents/plans/<name>.md` and keep it to
one page (about 600 words):

- **Goal:** what changes and why, in a sentence or two.
- **Done when:** observable checks (commands, tests, behavior).
- **Steps:** when another session or model will build it: ordered and concrete,
  naming the files and tests to change. Start a step with `human:` when it needs
  the person (a device, an account, a visual judgment).
- **Notes:** decisions, discoveries and what's next. Keep them current and
  short; put measurements and logs in files the plan links to.

A plan is about one session of work; bigger work becomes several plans. When a
plan is done, move it to `.agents/plans/done/`. If the work turns out to need a
different goal, stop and ask. Don't quietly redefine it.

## Roadmaps

A large project may have a one-page `ROADMAP.md`: a milestones table with exit
criteria, a session estimate and status; the decisions, accepted and open; and
a `## Plans and progress` table (`Milestone | Plan | Status`, one plan per row).
When it exists:

- A milestone's plans are `.agents/plans/m<N>-<slug>.md` and stay within its scope.
- Never settle a decision the roadmap lists as open. A spike plan measures the
  options and writes the ADR in `docs/adr/` as *Proposed*. Only the human accepts
  it, and plans that depend on it are written after that.
- Edit `ROADMAP.md` only for its status and plan tables, unless the human agrees
  to change scope, order or targets.
- When a milestone has used its estimate, or a plan ends without reaching its
  goal, don't write another plan for it. Stop and offer the human three choices:
  close it and record the shortfall, cut scope, or continue with one named plan.
- When a milestone's exit criteria all hold, say so. The human closes it.
- When the human closes the last milestone, offer to retire the roadmap: move
  its accepted decisions to `docs/adr/README.md`, then delete `ROADMAP.md`.

## Review

Independent review is optional and human-triggered: `wf review` for code,
`wf audit <plan>` for a plan before it's built, or any other way the human
chooses. The reviewer should be a different model from the one that wrote the
work. It may use available tools to investigate and run checks under normal
harness permissions. Temporary test/build artifacts are allowed; isolate and
clean up artifacts it creates. It must not edit project files, apply fixes,
stage or commit changes, or mutate external systems. Don't start a review
yourself. After significant work, say whether you think it's worth reviewing.

Saved results are `.agents/reviews/<date-time>-review.md` (about code: fix the
code) and `<date-time>-audit.md` (about a plan: amend the plan). The latest is
the newest of those names; ignore files in other naming styles. Findings are advice, not orders. Check each one
against the code and tests. Fix the real ones and briefly say why you rejected
the rest.

## Done

Before calling work done: the relevant tests pass, the diff contains only the
intended change, and any remaining risk is stated plainly.
<!-- wf:end -->
