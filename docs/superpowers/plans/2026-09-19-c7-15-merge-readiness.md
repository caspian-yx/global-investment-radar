# C7.15 Final Integration & Acceptance Closure

## Goal

Close the remaining product-level Golden QA gaps on the existing C7 Web Report PR without changing Admin Console, canonical intelligence semantics, Evidence, Claim, Event, snapshot identity, or ranking truth.

## Constraints

- Keep PR #1 open and unmerged.
- Use the existing `run_daily -> report_context -> Web projection -> renderer` path for the final Golden report.
- Keep Email rendering separate and backward compatible.
- Keep China capabilities fail-closed when production-backed data is unavailable.
- No entity-specific freshness rules, Golden hard-coding, or manual HTML edits.

## Workstreams

1. Add generic story lifecycle freshness evidence at the Web projection and ranking boundary: Day 0 eligibility, repeated no-information-gain decay, material follow-up reactivation, and user-visible ranking/section decisions.
2. Make market trend ranges real for 1D/1W/1M, expose VIX/VXN levels and multi-day context, and add compact data-backed risk context without adding a second dashboard.
3. Select the second major visual deterministically by information value; keep the total budget at two and keep missing history fail-closed.
4. Add interaction/static QA coverage for range controls, selection, tooltips, touch, keyboard focus, reduced motion, JS-disabled readability, and mobile tape behavior.
5. Add generic lifecycle and Golden-report regressions, regenerate the 2026-09-17 Golden through the real pipeline, run all quality gates, push a normal commit to the PR branch, and wait for CI.

## Acceptance evidence

The final report must document freshness decisions, final story ordering, VIX/VXN and rates context, visual-slot decisions, interaction behavior, China capability truth, canonical immutability checks, full local gates, CI result, and remaining accepted limitations. Do not merge.
