# Changelog

All notable changes to attenu-derive are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project adheres to Semantic Versioning.

## [Unreleased]

### Added
- Supply chain: SLSA build provenance (sigstore attestation) on every release; OpenSSF Scorecard weekly and on push.

### Fixed
- `attenu init` no longer requires `--product`: it keeps an existing product's name, else names the product after the directory. The README quick start shows the flag.
- `attenu demo` in a directory without a product exits 2 with one line saying to run `attenu init`, and writes nothing (it used to crash with a traceback and leave a partial ledger).
- `attenu demo` ends with the three next commands and the real paths it wrote: `attenu-guard view`, `attenu verify`, `attenu report`. The JSON stays on stdout; the next block goes to stderr.
- `attenu ui` says the truth: the local console is not published yet, and `attenu report` produces the HTML evidence report today.
- `attenu link` and `attenu sync` without the cloud client say the control plane is not published yet, instead of pointing at a package that is not on PyPI.

## [0.2.1] — 2026-08-26

### Changed
- Packaging: `[project.urls]` (homepage, docs, source, issues, changelog) so PyPI shows them; PyPI classifiers; the package summary and README opener now state what the engine does in one sentence. No code changes.

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
