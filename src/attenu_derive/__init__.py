"""attenu-derive — task -> minimal enforceable authority (proposal); attenu-guard's meet disposes.

Public SDK:
    from attenu_derive import Deriver, DelegationEvent, load_catalog, load_domain
    Deriver(domain=load_domain("retail-support"), operator_grants={"mail.send"}).propose(event)
CLI: `attenu onboard <traces> --domain <pack>` / `attenu coverage` / `attenu verify <bundle> --hs256-key <hex>`.
"""
__version__ = "0.1.0"

from attenu_derive.derive.propose import Deriver, DelegationEvent, event_from_row, spec_to_authority
from attenu_derive.catalog.coverage import load_catalog, load_domain, resolve, coverage

__all__ = ["Deriver", "DelegationEvent", "event_from_row", "spec_to_authority",
           "load_catalog", "load_domain", "resolve", "coverage", "__version__"]
