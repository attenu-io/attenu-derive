# Corpus schema v1 (one JSONL row per delegation event) — DERIVED FEATURES ONLY (ADR-05)

`event_id, source (observed|dataset|human), project, framework, task_hash, task_features,
parent_authority, tools_available[{name, schema_fingerprint}],
child_calls[{tool, scope_class, quantity_buckets, arg_shape, arg_hashes(salted), outcome}],
derived_min_authority (label), label_provenance, label_review (rubric_version, adjudicated_by, agreement),
split (by PROJECT: train|dev|test), licence, run (model, seed, versions), manifest_hash`

Never present: raw task text in shipped corpora (hash + features; raw kept only in the local
mirror for gold review), argument values, prompts, payloads.
