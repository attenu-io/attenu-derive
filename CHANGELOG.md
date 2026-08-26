# Changelog

All notable changes to attenu-derive are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project adheres to Semantic Versioning.

## [Unreleased]

- Packaging: `[project.urls]` (homepage, docs, source, changelog) so PyPI shows them.

## [0.2.0] — 2026-08-25

### Changed — BREAKING
- **Open engine.** Licence is Apache-2.0. The control plane left the package: `attenu link` / `attenu sync`, the
  installation token, the flywheel export and the Attenu issuer keys now live in the optional `attenu_cloud`
  client (shipped with the Attenu console). `attenu link` / `sync` / `ui` print an install hint when it is absent.
- **Enforcement needs no token.** `enforce` and `shadow` run without any licence check; observe → shadow → enforce
  is one flag each way, offline.
- Config-revision verification trusts the product's own anchor key plus `attenu_derive.config.ISSUER_KEYS`
  (empty by default; the cloud client contributes the Attenu issuer keys when installed).

### Added
- `README.md` for the public release; `AGENTS.md` for coding agents; this changelog.
