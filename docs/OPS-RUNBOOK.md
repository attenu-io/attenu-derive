# Attenu ops runbook

*Install → shadow → curate → enforce → verify, for an operator running Attenu on their own agent app. All
paths assume the packaged `attenu` CLI (installs from a wheel, no source tree).*

## Install

    pip install attenu-derive            # brings delegation-guard (the enforcement shim) as a dependency
    attenu --version

## 1. Shadow first (zero risk)

Run your app with the delegation-guard adapter for your framework in **observe** mode (see
`delegation-guard` adapter docs). It records every delegation + tool call to the audit ledger, redacted at
capture (names, scope classes, quantity buckets, salted hashes — never values). Nothing is blocked.

## 2. Day-0 coverage + a draft pack

    attenu onboard <recorded-traces>.jsonl --domain my-app --scaffold my-app.yaml

Prints what the shipped kit resolves (reads granted heuristically; money/mail/delete/exec **withheld**;
unknowns fail-closed) and writes `my-app.yaml` — a **draft** domain pack with one entry per uncurated tool
(heuristic guess + a `_review` note; tier-2 auto-marked `requires_grant`). **Edit every entry**: confirm the
scope, drop `requires_grant` only for tools you deliberately enable. This is the ≤1h step.

## 3. Verify the pack

    attenu coverage <traces>.jsonl --domain my-app     # expect curated_share high, unresolved 0

Re-run until unresolved is 0 and the tier-2 tools show as `requires_grant` (held), not withheld/unresolved.

## 4. Enforce

Install the shim in **enforce** mode with the derived authority (see `run_adk_enforce` for the wiring:
deriver + pack + `operator_grants` → `meet` → shim). Grant the tier-2 scopes the app legitimately needs
(`operator_grants={"payments.transfer"}`); leave the rest held. A call outside the granted authority is
denied before the tool body runs, the machine-readable denial goes back to the model, and a `deny` lands on
the ledger.

## 5. Export + verify evidence (offline)

    # the app exports a bundle from its audit log (delegation_guard.evidence.export_bundle)
    attenu verify bundle.json --hs256-key <hex>        # or an Ed25519 verifier in production

Returns `{integrity, monotonicity, containment}`. An auditor runs this against the bundle **with no access
to the engine** — a rewritten-and-re-signed log still fails, because the invariants are checked against the
bundle's own contents, not a hash.

## Operational controls

- **Revocation:** `guard.revoke()` / `guard.revoke_agent(agent_id)` cuts a node or a principal chain-wide.
- **Strike policy (optional):** `Guard.issue(strikes=StrikePolicy(n=3, mode="same_scope"))` auto-revokes a
  node after N same-scope denials; off by default; per-installation config.
- **Key rotation (ledger anchor):** rotate the Ed25519 signing key on your schedule; anchors are per-run, so
  a rotation does not invalidate past anchors (each verifies against the key that signed it).
- **TTL:** authorities carry a TTL; expired authority denies (`TTL_EXPIRED`).

## Failure modes

- **A benign call is denied** → the pack is missing/too tight for that tool. `attenu coverage` to find it,
  add/loosen the curated entry (never below the correct tier), re-verify, redeploy. Fail-closed by design.
- **A `requires_grant` tool is blocked** → expected until the operator enables its scope (`operator_grants`).
- **`attenu verify` fails integrity** → the bundle was altered or the anchor key is wrong. Do not trust the log.
- **`attenu verify` fails monotonicity/containment** → the ledger claims a delegation/action outside authority;
  treat as a tampered or buggy producer, not an Attenu enforcement result.
