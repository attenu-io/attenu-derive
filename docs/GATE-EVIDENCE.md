# Gate evidence pack — G1–G5

*Assembled 2026-08-19 at engine `8e0af26`. This pack leads with what it does NOT prove, on purpose: the
recurring failure mode across this build was a selected number becoming the headline, and every instance was
caught only by looking. A pack that names its limits first cannot be read as overclaiming — and it is the
version that survives a bank's security review rather than merely passing ours. Numbers reproduce from the
committed gold + the local corpus (mirrored to the private bucket).*

## Bounds and honest limitations (read first)

- **G3 volume is not met: 438 real delegation events against a 2,000 target.** The normalized public-dataset
  target (≥10,000) is met at 18,450, but the real-trace target is not. A direct consequence of deliberately
  stopping the sampling run when its data went stale. Rafael's decision (options in §G3).
- **Every customer-domain app measured is Google ADK.** customer-service and financial-advisor are both ADK
  on Haiku/Sonnet via LiteLLM. Framework diversity exists in the *code*-repo corpus (4 frameworks) but not in
  the customer-domain slice. The CrewAI/LangChain/Claude-SDK adapters are tested, but not on a customer-domain
  *app*.
- **The G1 hold-out over-provision metric rests on 13 clean rows.** The express hold-out has 43 rows but only
  13 survive the truncated/degenerate exclusion; the unused-scope and over-provision figures are computed on
  those 13. Small sample.
- **The finance-advisory pack was curated from the very traces it is scored on.** financial-advisor shows
  100% curated because we wrote its pack from its own tool set — that is onboarding, not a blind test. The
  honest out-of-sample curation number is customer-service (0% → 100% after curation) and the day-0 coverage
  on datasets the catalog never saw (§G5, and the BFCL/hermes/ToolACE out-of-sample numbers).
- **No live payment denial.** Enforce is proven live single-agent and across a chain, but the specific
  "a booking agent's `process_payment` is denied live" run was not done (21-agent app, low marginal value).
  The deriver holding `process_payment` until `--grant payments.transfer` is pinned offline
  (`tests/test_onboarding.py`), not shown live.
- **L3 (the LLM proposer) was never built.** Its trigger — a domain whose day-0 path cannot produce a
  confident curated grant — was never met: on the two customer domains measured, L2 (catalog + curation)
  resolved everything. The architectural finding (templates are a code-agent artifact; the catalog
  generalises) is settled; L3 stays deferred until a real gap appears.
- **All sampling is Haiku-class** except the two live Sonnet enforce runs. Ceilings derived from Haiku
  over-exploration may be looser than a frontier model needs (a small frontier calibration slice is planned).

## Summary

| Gate | Criterion (short) | Status | Headline (see bounds above) |
|---|---|---|---|
| G1 | Derivation works on real tasks | **MET** (on 13 clean hold-out rows) | benign-deny 0.0, unused 5.8%, escalation 0 |
| G2 | Protects real projects (enforce) | **MET** (ADK customer-domain; live) | 21/21: 0 benign blocks; over-reach 8,783/8,783; injection 0 widened; live single + chain; Haiku ≡ Sonnet |
| G3 | Data flywheel / volume | **NOT MET** (real events) / MET (datasets) | 438 / 2,000 real events · 18,450 / 10,000 dataset rows |
| G4 | Production grade | **PARTIAL** | onboarding ≤1h MET; service/SDK/runbook/security-review outstanding |
| G5 | Day-0 story | **MET** (out-of-sample) | held-out app: safe day-0, 100% curated in minutes |

## G1 — derivation works

Hold-out (project-level, express held out; **13 clean rows**), chain scoring: **benign-deny 0.0 ·
unused-scope 5.8% · over-provision 3 · escalation 0** (thresholds ≤2% / ≤20% / esc 0).
`python -m attenu_derive.eval.g1 --holdout express --check`

## G2 — protects real projects

- **Enforce (offline, 21 projects incl. 2 ADK customer-domain):** 0 benign blocks; 32 role-violation
  over-reaches correctly blocked. `python -m attenu_derive.eval.enforce --all`
- **Adversarial over-reach:** 8,783 / 8,783 = **100%**, scope-class 100%, 0 misses.
  `python -m attenu_derive.eval.adversarial --all`
- **Task-string injection:** 43,128 poisoned variants, **0 widened, 0 escalations** (24 correctly narrower).
  `python -m attenu_derive.eval.injection --all`
- **Live, single agent (Haiku + Sonnet):** a real customer-service agent denied mid-run on a held scope;
  denial contract observed; ledger anchored. `docs/LIVE-ENFORCE.md`.
- **Live, delegation chain (Haiku):** analyst `{web.fetch}` ⊂ coordinator, denied `web.search` mid-chain;
  ledger anchored across the chain. `docs/LIVE-ENFORCE.md` (chain section).
- **Structural, not model-dependent:** Haiku ≡ Sonnet — enforcement does not inherit the model's judgment.
  (Bound: both are ADK.)

## G3 — data flywheel

- Real corpus: **438 delegation events / 599 rows / 21 projects × 4 frameworks** (target ≥ 2,000 — **NOT met**).
- Normalized datasets: **18,450 rows** (BFCL + hermes + ToolACE; target ≥ 10,000 — MET).
- Versioned corpus + eval harness gate every release in CI (G1 + adversarial + injection gates + corpus lint). MET.
- **Decision for Rafael:** (a) resume bounded sampling with the improved harnesses; (b) restate the event
  target and let the dataset rows carry volume (2,000 was always a proxy for richness); (c) record the gap.

## G4 — production grade

- **Onboard in ≤ 1 hour:** MET — travel-concierge to 100% curated in minutes following `docs/ONBOARDING.md`.
- Threat model, denial contract, strike policy, ledger anchoring (ADR-14): filed and tested.
- **Outstanding (T33):** installable service + SDK, config, delegation-graph / evidence-export view, ops
  runbook, security review + red team on the derivation input, perf numbers. G4 is PARTIAL until these land.

## G5 — day-0 story

A brand-new app held out of all training (travel-concierge) gets **safe** derivation from the shipped kit
alone: all payment tools withheld, unknowns fail-closed, reads granted heuristically — shadow-ready with
zero unintended-payment risk. Curation then takes it to 100% with money tools held pending an operator
grant. `docs/ONBOARDING.md`; `tests/test_onboarding.py`. Out-of-sample catalog coverage on public datasets
the catalog never saw is the corroborating number (BFCL/hermes/ToolACE, `catalog.coverage`).

## Reproduce everything

`python -m pytest -q` runs the full suite including the **injection + adversarial CI gates** and the
**corpus lint** (schema-drift guard). `python -m attenu_derive.corpus.lint` → 0 violations.
