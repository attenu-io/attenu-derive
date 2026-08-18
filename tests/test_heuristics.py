from attenu_derive.catalog.heuristics import heuristic_resolve
from attenu_derive.catalog.coverage import load_catalog, resolve


def test_families():
    assert heuristic_resolve("calculate_triangle_area")["scope"] == "compute.pure"
    assert heuristic_resolve("math.factorial")["scope"] == "compute.pure"
    assert heuristic_resolve("get_user_tickets")["scope"] == "data.read"
    assert heuristic_resolve("create_ticket")["scope"] == "data.write"
    assert heuristic_resolve("delete_message")["scope"] == "data.delete"
    assert heuristic_resolve("post_tweet")["scope"] == "mail.send"
    assert heuristic_resolve("book_flight")["scope"] == "payments.transfer"
    assert heuristic_resolve("fetch_url_content")["scope"] == "web.fetch"
    assert heuristic_resolve("search_engine_query")["scope"] == "web.search"
    assert heuristic_resolve("cd")["scope"] == "fs.read" and heuristic_resolve("mkdir")["scope"] == "fs.write"
    assert heuristic_resolve("rm")["scope"] in ("fs.write", "fs.delete")
    assert heuristic_resolve("frobnicate_zorb") is None


def test_resolve_prefers_exact_then_pattern_then_heuristic_and_flags_it():
    cat = load_catalog()
    assert resolve(cat, "read_file").get("heuristic") is None
    assert resolve(cat, "mcp__x__y")["scope"] == "unknown.mcp"
    h = resolve(cat, "get_weather_forecast"); assert h["scope"] == "data.read" and h["heuristic"] is True
    assert resolve(cat, "get_weather_forecast", heuristics=False) is None


def test_camelcase_and_prefixed_names_are_tokenized():
    from attenu_derive.catalog.heuristics import tokenize
    assert tokenize("Movies_3_FindMovies") == "movies 3 find movies"
    assert tokenize("pressBrakePedal") == "press brake pedal"
    assert heuristic_resolve("Movies_3_FindMovies")["scope"] == "data.read"
    assert heuristic_resolve("Weather_1_GetWeather")["scope"] == "data.read"
    assert heuristic_resolve("Hotels_4_SearchHotel")["scope"] == "data.read"
    assert heuristic_resolve("Restaurants_2_ReserveRestaurant")["scope"] in ("payments.transfer", "data.write")


# ---- T7 (PM W2 slice, 2026-08-18): catalog v1 -------------------------------------------------------
def test_read_verbs_beat_noun_matched_tier2_families():
    """The class: a tier-2 family (payments/mail/delete/write) firing on a NOUN in a tool whose VERB is a read.
    `get_order_details` -> payments.transfer was a benign-deny generator (G1 under-provisioning)."""
    for name in ("get_order_details", "order_status_check", "library.search_book", "openlibrary.books_search",
                 "nature_reserve.find_nearby", "hotel_booking.check_availability", "restaurant_search.find_closest",
                 "get_settings", "list_payments", "check_booking_status", "get_closest_store"):
        h = heuristic_resolve(name)
        assert h and h["scope"] == "data.read" and h["tier"] == 0, (name, h)
    # ...and the fix never widens: write/money verbs still land in their tier-2 family
    assert heuristic_resolve("place_order") == {"scope": "payments.transfer", "tier": 2, "heuristic": True, "rule": heuristic_resolve("place_order")["rule"]}
    assert heuristic_resolve("cancel_booking")["tier"] == 2
    assert heuristic_resolve("delete_message")["scope"] == "data.delete"
    assert heuristic_resolve("send_message")["scope"] == "mail.send"


def test_device_actuation_family():
    for name in ("pressBrakePedal", "fillFuelTank", "startEngine", "lockDoors", "setCruiseControl", "play_song",
                 "Music_3_PlayMedia", "spotify.play", "connectBluetooth", "oven_preheat", "ControlAppliance.execute"):
        h = heuristic_resolve(name)
        assert h and h["scope"] == "device.actuate" and h["tier"] == 1, (name, h)
    assert heuristic_resolve("displayCarStatus")["scope"] == "data.read"          # reads of device state stay reads
    assert heuristic_resolve("check_tire_pressure")["scope"] == "data.read"


def test_compute_lookup_write_and_handoff_families():
    for name in ("predict_house_price", "solve_quadratic", "run_linear_regression", "t_test", "compound_interest",
                 "liter_to_gallon", "generate_DNA_sequence", "sentiment_analysis", "geometry.circumference", "diabetes_prediction"):
        assert heuristic_resolve(name)["scope"] == "compute.pure", name
    for name in ("stock_price", "weather_forecast", "latest_exchange_rate", "air_quality", "movie_details.brief", "sports_ranking"):
        assert heuristic_resolve(name)["scope"] == "data.read", name
    assert heuristic_resolve("run_tests")["scope"] == "code.exec"                 # "run" without a math noun stays exec
    for name in ("todo", "record", "inventory_management", "reschedule_event", "game.save_progress", "log_food", "add_stock_to_watchlist"):
        assert heuristic_resolve(name)["scope"] == "data.write", name
    for name in ("handover_to_human_agent", "transfer_to_human_agent", "contact_customer_support"):
        assert heuristic_resolve(name)["scope"] == "agent.message", name
    assert heuristic_resolve("retweet")["scope"] == "mail.send"
    assert heuristic_resolve("frobnicate_zorb") is None                           # still fail-closed on nonsense


def test_destructive_and_payment_instrument_writes_stay_tier2():
    """Audit of the T7 rewrite: a hard-delete verb dominates wherever it sits; a write on a payment instrument is tier 2."""
    assert heuristic_resolve("todo_delete")["scope"] == "data.delete"
    assert heuristic_resolve("register_credit_card")["scope"] == "payments.transfer"
    assert heuristic_resolve("add_payment_method")["scope"] == "payments.transfer"
    assert heuristic_resolve("update_order")["scope"] == "payments.transfer"
    assert heuristic_resolve("create_ticket")["scope"] == "data.write"          # support ticket: not money
    assert heuristic_resolve("get_credit_card_balance")["scope"] == "data.read"  # reads stay reads


def test_bucket_review_fixes():
    assert heuristic_resolve("Homes_2_FindHomeByArea")["scope"] == "data.read"          # find is a lookup even next to "area"
    assert heuristic_resolve("is_prime")["scope"] == "compute.pure"
    assert heuristic_resolve("nfl_data.player_record")["scope"] != "device.actuate"
    assert heuristic_resolve("get_outside_temperature_from_google")["scope"] == "data.read"
    assert heuristic_resolve("duck_duck_go")["scope"] == "web.search" and heuristic_resolve("web_search")["scope"] == "web.search"
    assert heuristic_resolve("create_histogram")["scope"] == "compute.pure"
    assert heuristic_resolve("set_navigation")["scope"] == "device.actuate"


def test_out_of_sample_generalizations_from_hermes_and_toolace():
    """T11 out-of-sample review (hermes / ToolACE were not used to curate v1): agent-nouns are computation;
    WEAK mail words (comment/email/contact) must not beat a read verb or an agent-noun; strong ones still do."""
    for name in ("ExpertQAExtractor", "Toxic Comment Detector API", "Bouncer Email Checker", "QR Code Generator", "email_validator", "transcribe_audio"):
        assert heuristic_resolve(name)["scope"] == "compute.pure", name
    assert heuristic_resolve("Abuse Contact Lookup")["scope"] == "data.read"
    assert heuristic_resolve("track_expenses")["scope"] == "data.write"
    for name in ("post_social_media_status", "send_email", "email_report", "comment", "contact"):
        assert heuristic_resolve(name)["scope"] == "mail.send", name                    # strong verbs / bare weak verbs stay tier 2
    assert heuristic_resolve("execute_program")["scope"] == "code.exec"
    for name in ("SEC Filings", "Timezones", "Airports in a Metro"):
        assert heuristic_resolve(name)["scope"] == "data.read", name                    # verb-less plural lookup nouns
    assert heuristic_resolve("United States Transit Stations Mobility API")["scope"] == "data.read"
    assert heuristic_resolve("Commify") is None                                         # nonsense stays unresolved
