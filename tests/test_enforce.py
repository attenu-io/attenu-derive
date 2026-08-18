"""T28: enforce evidence — real Guards with derived authority + domain packs; 0 benign blocks, over-reach blocked."""
from attenu_derive.eval.enforce import enforce_project, PROJECT_DOMAINS
from tests.test_shadow import ROOT, CHILD


def test_enforce_project_reports_benign_and_overreach():
    rep = enforce_project([[ROOT, CHILD]], "some-code-project")
    assert rep["benign_calls"] > 0
    assert rep["overreach_injected"] > 0 and rep["scope_blocked_rate"] == 1.0
    assert rep["overreach_blocked_rate"] >= 0.95


def test_customer_service_domain_is_configured_for_enforce():
    assert PROJECT_DOMAINS["adk-customer-service"][0] == "retail-support"
    assert "mail.send" in PROJECT_DOMAINS["adk-customer-service"][1]        # operator granted its send_* tools


def test_operator_grant_gate_is_not_vacuous():
    """Without the mail.send operator grant, the customer-service send_* calls ARE benign-blocked (held pending grant);
    granting it lets the workload pass. The gate does real work."""
    import json, glob
    from attenu_derive.eval import enforce as E
    runs = [[json.loads(l) for l in open(f) if l.strip()] for f in glob.glob("data/mirror/adk-customer-service-*.jsonl")]
    if not runs:
        import pytest; pytest.skip("customer-service mirror not present")
    saved = E.PROJECT_DOMAINS["adk-customer-service"]
    try:
        E.PROJECT_DOMAINS["adk-customer-service"] = ("retail-support", set())
        without = E.enforce_project(runs, "adk-customer-service")
        E.PROJECT_DOMAINS["adk-customer-service"] = ("retail-support", {"mail.send"})
        with_grant = E.enforce_project(runs, "adk-customer-service")
    finally:
        E.PROJECT_DOMAINS["adk-customer-service"] = saved
    assert without["benign_blocks"] > 0 and all(b[2] == "mail.send" for b in without["benign_block_detail"])
    assert with_grant["benign_blocks"] == 0
