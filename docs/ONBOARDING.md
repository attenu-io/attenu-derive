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
   must show as **held** until you grant them. On the ledger these are three different words — a deny's
   `disposition` is `held_pending_grant` (waiting on you), `unresolved` (declare the tool) or
   `out_of_authority` (real over-reach) — so "held" never reads as "denied" in any view.
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

## The loop as one CLI sequence (A2a)

    # 1. shadow: run your app with the observe adapter -> traces.jsonl (nothing blocked)
    # 2. day-0 report + a draft pack for the gaps:
    attenu onboard traces.jsonl --domain my-app --scaffold my-app.yaml
    # 3. EDIT my-app.yaml — the judgement step (see cost below) — then move it into catalog/domains/
    # 4. verify: attenu coverage traces.jsonl --domain my-app   (expect unresolved 0, tier-2 requires_grant)
    # 5. enforce with the pack + operator_grants; export evidence with attenu_derive.flywheel.export_for_flywheel
    # 6. an auditor runs: attenu verify bundle.json --pubkey <hex>   (integrity / subset / containment)

## Onboarding cost — measured (A2c)

Across the three onboarded apps (retail-support, finance-advisory, travel-planning) — **23 tools total, 7
(30%) required an operator judgement call**, the other 70% were mechanical confirmations of the scaffold's
heuristic guess. A "judgement call" = day-0 was withheld or unresolved, or a tier-2 grant decision. Examples:

| app | judgement call | the decision |
|---|---|---|
| retail-support | `send_care_instructions`, `send_call_companion_link` | tier-2 `mail.send` — hold as `requires_grant`? **yes** |
| retail-support | `modify_cart` | heuristic said payments; a cart write is `data.write`, not money — **corrected** |
| travel-planning | `create_reservation`, `process_payment` | tier-2 `payments.transfer` — hold as `requires_grant`? **yes** |
| travel-planning | `payment_choice` | lists options, does not move money → `data.read` (not payments) — **judgement** |
| travel-planning | `memorize` | day-0 unresolved → internal scratch `state.write` — **judgement** |

So the curation burden is **~30% of an app's tools need a human decision, and it is front-loaded on the
tier-2 / money tools** — exactly the ones a bank wants a human to decide anyway. Day-0 is safe before any of
it (money withheld, unknowns fail-closed), and the wall-clock is minutes. This is the register's
"curation-burden scales with customers" risk, now a number rather than a worry: it scales with an app's
*distinct sensitive tools*, not its traffic, and the scaffold does the mechanical 70%.
