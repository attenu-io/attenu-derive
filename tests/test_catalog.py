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


# ---- T7 (PM W2 slice, 2026-08-18): honest coverage metric + catalog v1 -----------------------------
def test_coverage_splits_grantable_withheld_unresolved():
    """Headline = GRANTABLE (curated + heuristic tier<=1); tier-2 heuristics resolve but the deriver never grants them."""
    cat = load_catalog()
    rows = [{"child_calls": [{"tool": "read_file"}, {"tool": "get_order_details"}, {"tool": "place_order"}, {"tool": "frobnicate_zorb"}]}]
    cov = coverage(rows, cat)
    assert cov["calls"] == 4
    assert cov["calls_curated_share"] == 0.25
    assert cov["calls_grantable_share"] == 0.5
    assert cov["calls_withheld_share"] == 0.25
    assert cov["calls_unresolved_share"] == 0.25
    assert cov["calls_covered_share"] == 0.75                    # resolvable — kept, not the headline
    assert cov["withheld_tools_top"] == {"place_order": 1}
    assert cov["events_grantable_share"] == 0.0


def test_catalog_v1_curates_common_tier2_verbs_and_shell_commands():
    cat = load_catalog(); assert cat["version"] >= 1
    for tool, scope in (("send_email", "mail.send"), ("send_message", "mail.send"), ("post_tweet", "mail.send"),
                        ("book_flight", "payments.transfer"), ("cancel_order", "payments.transfer"),
                        ("cd", "fs.read"), ("cat", "fs.read"), ("mkdir", "fs.write"), ("rm", "fs.delete"),
                        ("Payment_1_MakePayment", "payments.transfer"), ("Buses_3_BuyBusTicket", "payments.transfer"),
                        ("Hotels_4_ReserveHotel", "payments.transfer"), ("Media_1_PlayMovie", "device.actuate")):
        e = resolve(cat, tool); assert e and e["scope"] == scope and not e.get("heuristic"), (tool, e)
    assert resolve(cat, "place_order")["heuristic"] is True       # deliberately NOT curated: pins the withheld path
    assert resolve(cat, "Hotels_4_SearchHotel")["scope"] == "data.read"   # patterns must not swallow the reads
