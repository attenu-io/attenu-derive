# Contributing

Thanks for helping make agent delegation safe.

## Where to ask

Questions, ideas and "here is what I built" go to [Discussions](https://github.com/attenu-io/attenu-derive/discussions).
Bugs and requests are [issues](https://github.com/attenu-io/attenu-derive/issues/new/choose). Vulnerabilities go to the
private advisory form only (see `SECURITY.md`). Anything else: https://attenu.io/contact/.

## Ground rules

- **DCO required.** Sign off every commit (`git commit -s`). By doing so you
  certify the [Developer Certificate of Origin](https://developercertificate.org/).
  We use DCO rather than a CLA so the project stays genuinely community-owned.
- **Apache-2.0.** All contributions are under the project license. Do not paste
  code under GPL/AGPL/BUSL or any copyleft/commercial-restriction license — it
  will be rejected (we keep the tree clean for downstream commercial use).
- **Tests are not optional.** Any change to `authority.py`, `chain.py`, or
  `guard.py` must keep `python tests/run_properties.py` green and add a case if
  it introduces a new behaviour. Invariant changes need a property, not an
  example.

## Scope of the open library

We happily take: new framework adapters, new ceiling types, audit-schema
consumers/exporters (SIEM connectors especially), performance, docs, and
hardening. We will politely redirect one thing: changes to *enforcement* — the
`Authority` algebra, ceilings, the audit log, the wire format, adapters — belong in
[`attenu-guard`](https://github.com/attenu-io/attenu-guard); this repository decides
*what* to grant, never *whether* a call runs. We take: domain packs, catalog entries,
evaluators, samplers for new frameworks, derivation-quality work, docs.

## Dev loop

```bash
python tests/run_properties.py     # zero-dep invariant check
pytest                             # full hypothesis suite (pip install -e '.[test]')
python examples/poisoned_summarizer.py
```
