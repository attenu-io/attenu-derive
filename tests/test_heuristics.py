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
