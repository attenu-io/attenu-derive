# Internal security review — derivation engine

*Structured internal pass against the five threat-model invariants (`docs/THREAT-MODEL.md`). **Internal, solo:
there is no second reviewer** — this is a self-review with findings and triage, not an external audit. An
external review is a pre-sale item (Phase C). Date 2026-08-19, engine `dc79cf4`.*

Method: for each invariant, name what enforces it, what a test proves, what a reviewer probed, and the
residual. Findings are triaged Pass / Concern / Action.

| # | Invariant | Enforced by | Test evidence | Reviewer probe | Verdict |
|---|---|---|---|---|---|
| I1 | No escalation past parent | `Authority.meet` (shim), closed vocab | G1 escalation 0 (gate); adversarial 8,783/8,783; injection 0 escalations / 43k variants | Tried to widen a child via poisoned task text and via a name-dropped scope | **Pass** |
| I2 | Authority from declared structure, never task text | scopes from `tools_available` ∩ role families; explorer robust to task verbs; `no_write` structural cap; delegate set from roster not prompt | injection gate (0 widened); `test_child_write_authority_is_never_inferred_from_task_text` | Re-checked the two historical widenings the injection suite found; both fixed + pinned | **Pass** |
| I3 | Over-reach denied + on ledger | shim `check` denies before the tool body; every allow/deny appended | adversarial (100% blocked); live enforce (`deny` on ledger, denial contract) | Confirmed a live denial (customer-service `send_*`) lands on the ledger and returns to the model | **Pass** |
| I4 | Tier-2 never inferred | `HEURISTIC_MAX_GRANT_TIER = 1`; tier-2 needs a curated entry or `requires_grant` | `test_read_verbs_beat_noun_matched_tier2_families`; day-0 on travel-concierge withheld all 3 payment tools | Verified a heuristic can never produce a payments/mail/delete/exec grant | **Pass** |
| I5 | Recorder is a non-channel | redaction at capture (ADR-05); salted per-run hashes | features property test; corpus rows carry no raw value/task | Grepped a live corpus row for the CRM values / emails in the workload — absent | **Pass** |

## Findings and residuals (triage)

- **CONCERN — I2, curated-catalog trust boundary.** A *curated* entry is trusted; a wrong one (a real payment
  tool mapped to `data.read`) under-restricts. *Mitigation in place:* heuristics — the un-reviewed surface —
  are barred from tier-2 (I4), so an un-reviewed error is bounded to tier-0/1. *Action (Phase A/B):* the
  `attenu onboard` scaffold marks every entry `_review` and auto-flags tier-2 `requires_grant`, so a curated
  mistake requires the operator to both edit the scope *and* remove the grant flag — two deliberate steps.
- **CONCERN — I3 ledger integrity depends on the anchor key.** The chain is tamper-*evident*, not
  tamper-proof; detection depends on an out-of-band anchor (ADR-14). *Mitigation:* `verify_anchor` + the
  offline `evidence.verify_bundle` catch a full rewrite. *Action (Phase B):* key management / anchor custody
  is a deployment decision, not yet a product feature.
- **ACTION — solo review is not external.** This pass is internal by construction. An external red-team on the
  derivation input (poisoned tasks) is scheduled in Phase C and should precede any production bank deployment.
- **BOUND — coverage is on the tested surface.** Every customer-domain app reviewed is ADK; the invariants are
  framework-independent by construction (they live in the shim/vocabulary), but only ADK is exercised live.

## Verdict

The five invariants hold under internal review, each with a passing test and a reviewer probe. The two
CONCERNs are trust-boundary / key-custody items with mitigations in place and Phase-B/C actions, not
enforcement gaps. **No invariant failed.** This does not substitute for an external review before a bank
deployment.

## Performance

Derivation is code, not a model call: `Deriver.propose` over 41 real events — **p50 0.038 ms, p95 0.074 ms,
max 0.178 ms** (budget < 50 ms). The model path (L3) is **N/A** — it was never built, because L2 (catalog +
curation) resolved every domain measured.
