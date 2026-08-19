# Gate evidence pack — G1–G5

*Assembled 2026-08-19 at engine `8e0af26`. This pack leads with what it does NOT prove, on purpose: the
recurring failure mode across this build was a selected number becoming the headline, and every instance was
caught only by looking. A pack that names its limits first cannot be read as overclaiming — and it is the
version that survives a bank's security review rather than merely passing ours. Numbers reproduce from the
committed gold + the local corpus (mirrored to the private bucket).*

## Bounds and honest limitations (read first)

- **G3 real-event volume is DEFERRED to real-customer data (Rafael's decision, 2026-08-19).** 438 real
  delegation events against a 2,000 target; the normalized-dataset target (≥10,000) is met at 18,450. The
  2,000 was always a proxy for corpus richness bought from ourselves; the flywheel (customer-exported bundles,
  never background telemetry) supplies real volume once a design partner runs the loop. "Deferred to
  real-customer data", not "not met". The 25%-non-code G3 amendment is parked with it.
- **Customer-domain *enforcement* is now shown on three frameworks (A3); customer-domain *observe-sampling*
  is ADK-only.** customer-service + financial-advisor were observed/scored on ADK; **CrewAI and LangGraph are
  additionally enforced live on a customer-domain (travel-booking) workload** — real delegation, a held
  `payments.transfer` denied live, offline-verifiable, no divergence (`docs/A3-FRAMEWORKS.md`). Bound that
  remains: those two are *configured* crews/graphs, not scraped third-party apps (crewAI-examples target an
  old API; LangGraph apps are heterogeneous) — a real third-party customer-domain app on a non-ADK framework
  is a design-partner activity. G1/G5 *derivation-quality* numbers remain ADK-observed.
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
- **The onboarding wall-clock was measured by the builder, not a naive operator.** The ≤1h / ~minutes figure
  (G4/G5) inherits expert bias: I knew the vocabulary and the tools. A genuinely naive onboarding — the true
  G4 number — is a design-partner activity that solo work cannot manufacture. The judgement points are real;
  the *time* is a floor, not a representative user's.

## Summary

| Gate | Criterion (short) | Status | Headline (see bounds above) |
|---|---|---|---|
| G1 | Derivation works on real tasks | **MET** (on 13 clean hold-out rows) | benign-deny 0.0, unused 5.8%, escalation 0 |
| G2 | Protects real projects (enforce) | **MET** (ADK customer-domain; live) | 21/21: 0 benign blocks; over-reach 8,783/8,783; injection 0 widened; live single + chain; Haiku ≡ Sonnet |
| G3 | Data flywheel / volume | **DEFERRED** (real events, Rafael) / MET (datasets) | 438 real events (target parked) · 18,450 / 10,000 dataset rows |
| G4 | Production grade | **NEAR-COMPLETE** | packaged onboarding ≤1h MET; SDK/CLI + runbook + internal review done; graph-UI + service-mode outstanding |
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

- Real corpus: **438 delegation events / 599 rows / 21 projects × 4 frameworks**. The ≥2,000 real-event target
  is **DEFERRED to real-customer data** (Rafael, 2026-08-19): 2,000 bought from ourselves was a proxy; real
  volume arrives via the flywheel — **customer-initiated evidence-bundle export, never background telemetry**
  (the custody story is the thing a bank buys). The 25%-non-code amendment is parked with it.
- Normalized datasets: **18,450 rows** (BFCL + hermes + ToolACE; target ≥ 10,000 — MET).
- Versioned corpus + eval harness gate every release in CI (G1 + adversarial + injection gates + corpus lint). MET.

## G4 — production grade

- **Onboard in ≤ 1 hour:** MET — travel-concierge to 100% curated in minutes following `docs/ONBOARDING.md`.
- Threat model, denial contract, strike policy, ledger anchoring (ADR-14): filed and tested.
- **Packaging (Phase A1):** installable wheel + `attenu` CLI (`onboard`/`coverage`/`verify`) — installs into a
  clean env with no source tree; onboarding re-measured **through the packaged path** at ~1s (`docs/OPS-RUNBOOK.md`).
- **Internal security review** vs the 5 threat-model invariants: all pass, 2 trust-boundary concerns with
  mitigations + Phase-B/C actions, labelled internal (`docs/SECURITY-REVIEW.md`). External review is Phase C.
- **Perf:** `Deriver.propose` p50 0.038 ms / p95 0.074 ms / max 0.178 ms over real events (budget <50 ms);
  model path N/A (L3 never built).
- **Ledger contract for the console (2026-08-19, slice 1 / Plan A):** held ≠ denied is now ON the ledger
  (`disposition` on every deny, in the model-facing denial, across all 12 adapters); products have an identity
  before a key (`attenu init` → `.attenu/product.json`, per-process boot id, assigned chain ids); the spool sink
  carries the ingest idempotency key; inside a product the anchor is signed with a **product-local Ed25519 key**
  and verified with the public key only (`attenu verify --pubkey`) — the HMAC test signer survives only outside a
  product and is labelled `attenu-anchor-TEST`. Repro: shim `python tests/test_core_v02.py`,
  `python tests/test_sinks_identity.py`, `python tests/test_adapters_contract.py`; engine
  `python -m pytest -q tests/test_disposition.py tests/test_product.py tests/test_run_adk_enforce.py tests/test_cli.py`.
- **Delegation-graph UI: DONE in local mode (2026-08-19, slice 1 / Plan B)** — the private `attenu-console` package
  (`attenu ui`): products → chains → the delegation graph with denials by disposition + **verified ✓** (integrity ·
  monotonicity · containment against the product's public key) → evidence download → **Decisions** (questions;
  *Grant scope* writes `.attenu/grants.json`, which `run_adk_enforce` and `attenu demo` read — the loop closes).
  Proven from a fresh venv with three wheels and no source tree; `attenu demo` (USD 0, no model) produces a real
  anchored ledger so the demo never needs a key.
- **Service mode / cloud deployment: BUILT (2026-08-19, slice 1 / Plan C)** — the same console in cloud mode
  (`attenu-console`, one container, Postgres with RLS FORCEd for a non-owner role + negative tests per table):
  accounts → products → environments → installations; self-serve expiring installation tokens (EdDSA, verified
  OFFLINE by `attenu_derive.license` against bundled public keys; gate at START on the real enforce runners; observe
  free); `attenu link` / `attenu sync` (spool drain keyed (installation, boot, chain, seq, hash), forks alerted,
  allow-list re-validated on ingest, anchors append-only, content-free heartbeats, grants pulled); Installations +
  Attenu admin. Verified end to end locally (token → link → sync → chains verified in the cloud) and from the built
  image. **Not yet hosted** — needs the free accounts in `attenu-console/docs/DEPLOY.md` (Neon, Fly/Cloud Run,
  WorkOS; Rafael). G4: the packaged local path and the cloud path both exist; what remains for "production grade" is
  slice 2 (signed config revisions + ceiling, KMS anchoring) and a hosted deployment.

## G5 — day-0 story

A brand-new app held out of all training (travel-concierge) gets **safe** derivation from the shipped kit
alone: all payment tools withheld, unknowns fail-closed, reads granted heuristically — shadow-ready with
zero unintended-payment risk. Curation then takes it to 100% with money tools held pending an operator
grant. `docs/ONBOARDING.md`; `tests/test_onboarding.py`. Out-of-sample catalog coverage on public datasets
the catalog never saw is the corroborating number (BFCL/hermes/ToolACE, `catalog.coverage`).

**Operating cost (measured, A2c):** across the 3 onboarded apps, **23 tools, 7 (30%) required an operator
judgement call**, 70% mechanical scaffold confirmations. The judgement calls are front-loaded on the tier-2
tools a bank *wants* a human deciding (payments, mail held `requires_grant`), so the curation burden scales
with an app's **distinct sensitive tools, not its traffic**, and day-0 is safe before any of it. Pinned ≤50%
by a test (`tests/test_onboarding.py`).

## Reproduce everything

`python -m pytest -q` runs the full suite including the **injection + adversarial CI gates** and the
**corpus lint** (schema-drift guard). `python -m attenu_derive.corpus.lint` → 0 violations.
