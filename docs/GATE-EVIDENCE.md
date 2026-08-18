# Gate evidence pack — G1–G5

*Assembled 2026-08-19 at engine `8e0af26`. Every row states the criterion, the current measured number, and
the exact command that reproduces it from a clean checkout. Numbers are from the committed gold + the local
corpus (mirrored to the private bucket). This is the pack the Gate review grades — pending Rafael's four
criterion amendments and the G3 decision (both noted below).*

## Summary

| Gate | Criterion (short) | Status | Headline number |
|---|---|---|---|
| G1 | Derivation works on real tasks | **MET** | hold-out benign-deny 0.0, unused 5.8%, escalation 0 |
| G2 | Protects real projects (enforce) | **MET** | 21/21 projects: 0 benign blocks; over-reach 8,783/8,783 = 100%; injection 0 widened / 0 escalations; **live** single + chain, Haiku ≡ Sonnet |
| G3 | Data flywheel / volume | **PARTIAL** | 438 real events (target 2,000) · 18,450 dataset rows (target 10,000 **met**) — Rafael's call |
| G4 | Production grade | **IN PROGRESS** | onboarding ≤1h **met** (minutes); packaging/service/SDK/runbook/security-review outstanding |
| G5 | Day-0 story | **MET** | travel-concierge (held out): safe day-0 (payments withheld, unknowns fail-closed) → 100% curated in minutes |

## G1 — derivation works

Hold-out (project-level, express held out), chain scoring: **benign-deny 0.0 · unused-scope 5.8% ·
over-provision 3 · escalation 0** (thresholds ≤2% / ≤20% / esc 0). Repro:
`python -m attenu_derive.eval.g1 --holdout express --check`

## G2 — protects real projects

- **Enforce (offline, 21 projects incl. 2 customer-domain):** 0 benign blocks; 32 role-violation over-reaches
  correctly blocked. `python -m attenu_derive.eval.enforce --all`
- **Adversarial over-reach:** 8,783 injected / 8,783 blocked = **100%**, scope-class 100%, 0 misses.
  `python -m attenu_derive.eval.adversarial --all`
- **Task-string injection:** 43,128 poisoned variants, **0 widened, 0 escalations** (24 correctly narrower).
  `python -m attenu_derive.eval.injection --all`
- **Live, single agent (Haiku + Sonnet):** a real customer-service agent denied mid-run on a held scope;
  denial contract observed; ledger anchored. `docs/LIVE-ENFORCE.md`; `data/reports/enforce-live/`.
- **Live, delegation chain (Haiku):** an analyst `{web.fetch}` ⊂ its coordinator, denied `web.search`
  mid-chain; ledger anchored across the chain. `docs/LIVE-ENFORCE.md` (chain section).
- **Structural, not model-dependent:** Haiku and Sonnet behave identically — enforcement does not inherit
  the model's judgment.

## G3 — data flywheel

- Real corpus: **438 delegation events / 599 rows / 21 projects × 4 frameworks** (target ≥ 2,000 events — NOT met).
- Normalized public datasets: **18,450 rows** (BFCL + hermes + ToolACE; target ≥ 10,000 — MET).
- Versioned corpus + eval harness gate every release in CI (G1 + adversarial + injection gates). MET.
- **Decision for Rafael:** (a) resume bounded sampling with the improved harnesses; (b) restate the
  event target and let the dataset rows carry volume (the 2,000 was always a proxy for richness);
  (c) record the gap explicitly. Consequence of the deliberate stop of the volume run.

## G4 — production grade

- **Onboard in ≤ 1 hour:** MET — travel-concierge curated to 100% in minutes following `docs/ONBOARDING.md`.
- Threat model (`docs/THREAT-MODEL.md`), denial contract (`docs/DENIAL-CONTRACT.md`), strike policy,
  ledger anchoring (ADR-14): filed and tested.
- **Outstanding:** installable service + SDK packaging, config, delegation-graph view, ops runbook,
  security review + red team on the derivation input, perf numbers. This is the remaining bulk (T33).

## G5 — day-0 story

A brand-new app held out of all training (travel-concierge) gets **safe** derivation from the shipped kit
alone: all payment tools withheld, unknowns fail-closed, reads granted heuristically — shadow-ready with
zero unintended-payment risk. Curation (a domain pack) then takes it to 100% with the money tools held
pending an operator grant. Repro + numbers: `docs/ONBOARDING.md`; `tests/test_onboarding.py`.

## Reproduce everything

`python -m pytest -q` runs the full suite including the **injection and adversarial CI gates** and the
**corpus lint** (schema-drift guard). `python -m attenu_derive.corpus.lint` → 0 violations.
