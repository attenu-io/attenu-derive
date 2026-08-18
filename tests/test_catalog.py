from attenu_derive.catalog.coverage import load_catalog, resolve, coverage


def test_catalog_resolves_exact_and_glob():
    cat = load_catalog()
    assert resolve(cat, "read_file")["scope"] == "fs.read"
    assert resolve(cat, "Write")["scope"] == "fs.write"
    assert resolve(cat, "mcp__crm__crm_query")["scope"] == "unknown.mcp"
    assert resolve(cat, "totally_unknown") is None


def test_coverage_counts_calls_and_events():
    cat = load_catalog()
    rows = [{"child_calls": [{"tool": "read_file"}, {"tool": "grep"}]},
            {"child_calls": [{"tool": "Write"}, {"tool": "mystery"}]}]
    cov = coverage(rows, cat)
    assert cov["calls"] == 4 and cov["calls_covered_share"] == 0.75
    assert cov["events"] == 2 and cov["events_fully_resolvable_share"] == 0.5
    assert cov["uncovered_tools"] == {"mystery": 1}
