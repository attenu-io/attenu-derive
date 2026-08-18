# Onboarding a new app to Attenu (day-0 kit)

*The ≤1-hour path (G4) from a brand-new agent app to enforce mode. Measured on `travel-concierge` (G5,
held out of all training) — see the walkthrough at the end.*

Attenu ships a **base catalog + templates + heuristics**. A brand-new app already gets *safe* derivation
from that alone: read tools resolve heuristically, tier-2 families (payments, mail, deletes, exec) are
**withheld** unless curated, and anything unknown fails closed. Onboarding is turning that safe-but-coarse
day-0 into a confident, curated grant for *this* app's tools.

## The five steps

1. **List the app's tools.** `declared_suites(root_agent)` (or read the code) — the leaf tools each agent
   calls, and the delegation graph. AgentTools are delegation, not tools.
2. **Run day-0 coverage.** `catalog.coverage.coverage(rows, cat)` (or classify each tool) tells you what
   the shipped kit does with them: `curated` / `heuristic` (grantable) / `withheld` (tier-2 heuristic) /
   `unresolved`. Everything not `curated` is a curation candidate.
3. **Write a domain pack** — `catalog/domains/<app>.yaml`, `{tool: {scope, tier}}`. One entry per tool the
   app owns. Rules:
   - a tool that moves money / sends / deletes / executes is **tier 2** and gets `requires_grant: true`
     — curated (named, known) but **held** until the operator explicitly enables its scope. This is the
     safe default: the operator opts in per scope, once, at install.
   - reads, lookups and pure computation are tier 0/1 and grant automatically.
   - never widen: if unsure whether a tool writes, mark it the higher tier. Fail-closed beats guessing.
4. **Verify: 0 benign blocks, tier-2 held.** `eval.enforce` (or a shadow run) with the pack: the app's
   own workload must show **0 benign blocks** on the scopes you granted, and the `requires_grant` tools
   must show as **held** until you grant them.
5. **Enforce.** Install the shim in enforce mode with the derived authority (`run_adk_enforce` shows the
   wiring). Grant the tier-2 scopes the app legitimately needs (`--grant payments.transfer`), leave the
   rest held. A tool call outside the granted authority is denied live, with the denial on the anchored
   ledger.

## What "held pending an operator grant" means

It is a third state, distinct from *denied* (unknown, fail-closed) and *auto-granted* (a read). A
`requires_grant` tool is **curated and named** — the operator sees exactly what it is and enables its
scope with one config flip (`operator_grants={"payments.transfer"}` / `--grant payments.transfer`). Until
then the tool is held: the agent is not silently denied a mystery, and the sensitive capability is not
silently on. This is the audit-friendly default a regulated buyer wants.

## Walkthrough: travel-concierge (G5, held out of all training)

Day-0, shipped kit only, 9 leaf tools:

| tool | day-0 (base kit) | onboarded (travel-planning pack) |
|---|---|---|
| `google_search` | curated → web.search | web.search |
| `event_booking_check`, `flight_status_check`, `weather_impact_check` | heuristic → data.read | data.read (curated) |
| `google_search_grounding` | heuristic → web.search | web.search |
| `payment_choice` | **withheld** (heuristic payments) | data.read — it only *lists* options |
| `create_reservation` | **withheld** (heuristic payments) | payments.transfer, **requires_grant** |
| `process_payment` | **withheld** (heuristic payments) | payments.transfer, **requires_grant** |
| `memorize` | **unresolved** (fail-closed) | state.write (internal scratch) |

Day-0 posture is already safe: every money tool is withheld and the unknown fails closed — an operator
could run in shadow immediately with zero risk of an unintended payment. Onboarding (the pack) then makes
`payment_choice` a read (it does not move money), keeps `create_reservation`/`process_payment` as
`payments.transfer` held pending grant, and resolves `memorize` to internal scratch. After the pack the
app is 100% curated with the two money tools held — one `--grant payments.transfer` from enforce.
