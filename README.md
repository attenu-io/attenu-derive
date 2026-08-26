# attenu-derive

**Attenu Derive computes the permission set each AI agent task needs from the app itself — roster, tools, observed calls — for `attenu-guard` to enforce on every tool call and sub-agent handoff.**

[attenu.io](https://attenu.io) · [Docs](docs/) · [`attenu-guard`](https://github.com/attenu-io/attenu-guard) (Attenu Guard, the library that enforces what this engine works out) · [Threat model](docs/THREAT-MODEL.md) · [What is proven](docs/GATE-EVIDENCE.md)

**Works with** LangGraph · LangChain / deepagents · OpenAI Agents SDK · Google ADK · Pydantic AI · CrewAI · AutoGen · Claude Agent SDK · smolagents · AWS Strands · LlamaIndex · Semantic Kernel · Agno — unmodified (table below).

`attenu-derive` reads your agent application — the agents you declared, their roster, the tools
each one has, what each task actually calls — and computes the permission set each task needs.
[`attenu-guard`](https://github.com/attenu-io/attenu-guard) enforces that set inside your process,
for every agent and every sub-agent handoff, and writes a tamper-evident audit log your auditor
verifies offline. You approve the permissions once. Payments and deletes wait for a person you name, as do mail
and code execution.

You do not write a policy, and you do not describe one in prose. The input is the app itself.

## How it works

1. **Observe.** Run your app with the guard in observe mode (free, records only). The recorder
   captures tool names, scope classes and quantity buckets — never argument values, never
   prompt text.
2. **Derive.** For each agent and task the engine proposes a minimal `Authority` (scopes +
   limits + expiry), off the hot path, in layers: **L1** structural templates (roster and tool
   list → scopes) · **L2** a versioned tool→scope catalog with domain packs (code, customer
   service, finance, travel) · **L4** fail-closed default — an unknown tool is denied, not guessed.
   (An **L3** constrained-model proposal is designed and deliberately not shipped; see below.)
3. **Meet.** The guard grants `parent.meet(proposal)`: a proposal can only ever *narrow* what the
   parent holds, so an engine error over-restricts and never widens. An agent hands on no more than
   it holds.
4. **Approve.** `attenu onboard` prints the day-0 report — what resolves, what is held for a named
   person, what is unknown — and drafts a domain pack for the gaps. In our own onboarding runs
   about 30% of tools needed a person to decide, front-loaded on the sensitive ones; the number from
   your own run is the real one.
5. **Enforce, prove.** Observe → shadow (derived permissions evaluated, nothing denied) → enforce.
   Rollback is one configuration change plus a restart. Every decision lands in the guard's
   hash-chained audit log; `attenu report` renders it, `attenu verify` checks an exported bundle
   with no access to us.

Permissions come from declared structure — never from prompt text. Prompt injection can talk the
model into *asking*; it cannot widen what the process is allowed to do.

## Supported frameworks

The engine feeds [`attenu-guard`](https://github.com/attenu-io/attenu-guard), whose adapters attach at each
framework's official extension points — the framework stays **unmodified**. Observe-mode recorders exist for
the same set.

| Framework | Adapter | Offline demo + tests | Enforced live on a real app |
|---|---|---|---|
| LangGraph / LangChain `create_agent` / deepagents | `attenu_guard.adapters.langgraph`, `.langchain` | ✓ | ✓ (travel-booking) |
| Google ADK | `.google_adk` | ✓ | ✓ (customer-service, financial-advisor, travel-concierge) |
| CrewAI | `.crewai` | ✓ | ✓ (travel-booking) |
| OpenAI Agents SDK | `.openai_agents` | ✓ | — |
| Claude Agent SDK | `.claude_sdk` | ✓ | live-verified sub-agent denial |
| Pydantic AI | `.pydantic_ai` | ✓ | — |
| AutoGen | `.autogen` | ✓ | — |
| smolagents | `.smolagents` | ✓ | — |
| AWS Strands | `.strands` | ✓ | — |
| LlamaIndex | `.llama_index` | ✓ | — |
| Semantic Kernel | `.semantic_kernel` | ✓ | — |
| Agno | `.agno` | ✓ | — |

Pinned versions run in CI; a weekly job tests the unpinned latest of every framework, so upstream breakage
is caught on our side first. MCP and A2A: the guard's wire format carries the chain across services;
server-side verification examples are on the roadmap.

## Quick start

```bash
pip install attenu-derive            # pulls attenu-guard; zero network needed to enforce
attenu init                          # product identity + a local Ed25519 anchor key (no cloud)
attenu demo --scenario fanout        # 9 agents, 18 tools, every disposition, a real anchored ledger — USD 0
attenu coverage  <observed-log>      # what the catalog resolves for these tool calls
attenu onboard   <observed-log>      # day-0 report + a draft domain pack for the gaps
attenu report                        # printable evidence report (HTML) for this product's chains
attenu verify    <bundle.json>       # offline verification of an exported evidence bundle
```

`attenu policy` / `config` / `grant` / `ceiling` manage the product's signed configuration
revisions: operator grants, declared tools, the ceiling of scopes this product may *ever* be
granted. `attenu link` / `sync` / `ui` connect to the optional [Attenu console](https://attenu.io);
the console is never in the deny path and nothing here needs it.

## What is measured — and what is not

- **0 benign blocks across 21 evaluation scenarios** on our own sample apps, after a one-time setup
  pass (`docs/GATE-EVIDENCE.md`).
- **Every over-reach attempt denied** in the adversarial suite; injection families × positions →
  0 permissions widened, 0 escalations. The scenarios are self-written; we say so. Both suites are
  CI gates (`eval/`).
- **Live**, on real applications: a customer-service agent denied mid-run on two different models
  with the same result, and a financial-advisor analyst held to `{web.fetch}` ⊂ its coordinator
  and denied `web.search` mid-chain (`docs/LIVE-ENFORCE.md`, `docs/A3-FRAMEWORKS.md` — Google
  ADK, CrewAI, LangGraph).
- **Day-0 on a held-out app** (travel-concierge, 21 agents): payments withheld, unknowns denied,
  minutes to full coverage (`docs/ONBOARDING.md`).
- p50 0.038 ms per check on the template path.
- **Not shipped:** L3 model-based derivation, plan-vs-action divergence scoring, fleet management.
  Not done: external security audit, SOC 2, penetration test — `docs/SECURITY-REVIEW.md` is a
  structured self-review, single reviewer, and says so.

## What leaves your environment

In local mode, nothing leaves your environment: no telemetry, no outbound calls; the only egress is
a redacted bundle your operator exports by hand. The local audit log holds names, scope classes,
quantity buckets and salted hashes — an export fails rather than ship a field it does not
recognise. Custody is yours: the anchor key is product-local by default; KMS/HSM custody is
validated against a real key (`docs/OPS-RUNBOOK.md`).

## What is different

Three things, in this order. The **input is the application** — declared agents, roster, tools, observed
calls — not a person writing or describing a policy. The result lives **inside the guard's meet**, so a
derived permission set can only narrow what the parent holds and an engine error can only over-restrict —
across every sub-agent handoff. And the **evidence verifies with the vendor absent**: the auditor checks
integrity, child ⊆ parent and containment from the exported bundle alone.

## Layout

- `src/attenu_derive/vocab/`    versioned closed vocabulary (scopes, ceilings, ttl buckets)
- `src/attenu_derive/catalog/`  tool→scope catalog + domain packs + coverage tooling
- `src/attenu_derive/derive/`   L1–L4 engine: `Deriver.propose(event) -> (Authority, DerivationRecord)`
- `src/attenu_derive/sample/`   observe-mode recorders over the attenu-guard adapters (redact at capture)
- `src/attenu_derive/eval/`     adversarial · injection · enforce · corpus-lint gates
- `src/attenu_derive/corpus/`   corpus schema, normalizers, gold labels, rubric
- `src/attenu_derive/report.py`, `evidence_out.py`  evidence reports and bundles
- `docs/`                       threat model, live-enforcement record, onboarding, operations, evidence

## Non-negotiables

No network call on the deny path · the recorder never stores argument values · corpus blobs never
in git · secrets never in the product · every derivation record carries the deciding layer.

## License

Apache-2.0 (see `LICENSE`). Contributions under the DCO. Security policy:
the private advisory form on GitHub.
