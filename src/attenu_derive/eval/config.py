"""
One scoring config for every evaluator (PM standing rule, 2026-08-18): shadow, adversarial and enforce grade the
SAME corpus, so they must derive with the SAME per-project config — the curated domain pack + operator grants a
customer would actually deploy. A single source here prevents the silent divergence that once hid the product's
best evidence behind an apparent failure.
"""
from __future__ import annotations

from attenu_derive.catalog.coverage import load_domain
from attenu_derive.derive.propose import Deriver

# {project: (domain-pack name, {operator-granted scopes})} — what the customer installs and enables.
PROJECT_DOMAINS = {
    "adk-customer-service": ("retail-support", {"mail.send"}),
    "adk-financial-advisor": ("finance-advisory", set()),
}


def deriver_for(project: str | None) -> Deriver:
    cfg = PROJECT_DOMAINS.get(project or "")
    if cfg is None:
        return Deriver()
    name, grants = cfg
    return Deriver(domain=load_domain(name), operator_grants=set(grants))
