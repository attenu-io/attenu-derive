# attenu-derive (PRIVATE)

The Attenu derivation engine: **task + tools available + parent authority → proposed minimal
`Authority`**, always granted as `parent.meet(proposal)` by `attenu-guard`, so error can only
over-restrict. Four layers off the hot path: L1 templates → L2 tool→scope catalog + retrieval →
L3 constrained model proposal (customer's own endpoint, hard timeout, circuit breaker) → L4
fail-closed default. In-process library; no service on the deny path (ADR-01).

Plan of record, design, targets and ADRs live one directory up in the vault:
`../00-pm-charter-and-plan.md`, `../01-build-and-training-design.md`,
`../02-target-projects-sample-and-protect.md`, `../03-infra-and-architecture-decisions.md`.

## Layout
- `src/attenu_derive/vocab/`    versioned closed caveat vocabulary (scopes, ceilings, ttl buckets) → generates the L3 output schema
- `src/attenu_derive/catalog/`  tool→scope catalog (versioned static artifact) + coverage tooling
- `src/attenu_derive/corpus/`   corpus schema, manifest (hashes/splits by project), normalizers, gold labels, rubric
- `src/attenu_derive/derive/`   L1–L4 engine: `Deriver.propose(event) -> (Authority, DerivationRecord)`
- `src/attenu_derive/sample/`   `attenu-sample` observe-mode recorder over the attenu-guard adapters (redacts at capture — ADR-05)
- `src/attenu_derive/eval/`     eval harness + eval cards (release gate)
- `src/attenu_derive/service/`  local CLI/API + hooks into `Guard.delegate()` call sites
- `docs/`                       engineering docs (the vault holds the plan of record)

## Non-negotiables (from the ADRs)
No network call on the deny path · recorder never stores argument values · corpus blobs never in
git (manifest only; blobs in the encrypted bucket) · no telemetry until after the Gate · secrets
never in the product · every derivation record carries the deciding layer.
