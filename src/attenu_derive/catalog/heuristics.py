"""
Heuristic tier of the tool→scope catalog (v1, TOKEN-based): when a tool name is neither an exact
entry nor a glob pattern, classify it into a vocabulary FAMILY from its name. Every heuristic
result is flagged `heuristic: True` (lower confidence; reported separately in coverage) — it is
a lead for catalog curation, not a curated fact. Unknown -> None (fail closed).

Grant rule (shared with the deriver, `HEURISTIC_MAX_GRANT_TIER`): a heuristic classification may
be GRANTED for tier 0-1 families only; tier-2 families (payments, mail.send, code.exec, deletes)
resolve-but-WITHHOLD — they need a curated catalog entry (standing decision 2026-08-18). Coverage
therefore reports `grantable` (curated + heuristic tier<=1) as the headline, `withheld` apart.

Why tokens, not substring regexes (T7, 2026-08-18): the v0 regex tier matched NOUNS and
substrings — `get_order_details` -> payments.transfer ("order"), `restaurant_search.find_closest`
-> data.delete ("clos*"), `get_settings` -> data.write ("set*"). Those are read tools; the noun
matched a tier-2 family and the deriver correctly withheld it: a benign-deny generator (G1
under-provisioning). The class is "a family decided by a noun/substring while the VERB says
otherwise". So: tokenize; the FIRST VERB token decides the family (read verbs first — they beat
every noun); a few CARRIER verbs (run/execute/make/handle/...) defer to the nouns; verb-less
names fall back to a noun scan; nonsense stays unresolved. Known limitation: a write verb used
as a noun at the end of a name (`match_schedule`, `store_price`) still reads as the verb —
tier-1 family mismatches, flagged heuristic, bounded by the parent's meet; curate when seen.
"""
from __future__ import annotations

import re

HEURISTIC_MAX_GRANT_TIER = 1

_TIER = {"data.read": 0, "compute.pure": 0, "fs.read": 0,
         "data.write": 1, "fs.write": 1, "web.fetch": 1, "web.search": 1, "device.actuate": 1, "agent.message": 1,
         "payments.transfer": 2, "mail.send": 2, "data.delete": 2, "fs.delete": 2, "code.exec": 2}

# ---- POSIX / GorillaFileSystem-style shell commands: only when the tool name IS the command ----
SHELL = {**{c: "fs.read" for c in ("cat", "cd", "ls", "pwd", "find", "grep", "wc", "tail", "head", "diff", "du", "echo", "sort", "tree", "stat", "less", "more")},
         **{c: "fs.write" for c in ("mkdir", "touch", "mv", "cp", "write", "append", "chmod", "chown", "ln")},
         **{c: "fs.delete" for c in ("rm", "rmdir", "unlink")}}

# ---- verbs, by family: the FIRST verb token in the name decides ---------------------------------
READ_VERBS = {"get", "list", "read", "retrieve", "fetch", "show", "display", "view", "check", "search", "find", "query", "filter",
              "lookup", "describe", "is", "has", "verify", "validate", "browse", "inspect", "monitor", "look", "locate", "count",
              "status", "info", "preview", "peek", "poll", "watch", "explore", "scan", "load", "obtain", "recommend", "detail",
              "details", "compare", "select", "who", "what", "which"}
COMPUTE_VERBS = {"calc", "calculate", "compute", "convert", "solve", "predict", "simulate", "analyze", "analyse", "integrate",
                 "differentiate", "estimate", "generate", "plot", "draw", "translate", "classify", "identify", "recognize",
                 "recognise", "detect", "optimize", "optimise", "minimize", "maximize", "normalize", "transform", "encode",
                 "decode", "encrypt", "decrypt", "hash", "parse", "format", "summarize", "summarise", "extract", "tokenize",
                 "embed", "measure", "mix", "shuffle", "reverse", "concat", "concatenate", "sum", "multiply", "divide", "subtract",
                 "factorize", "interpolate", "extrapolate", "compose", "render", "design", "assess", "determine", "derive", "math",
                 "train", "fit", "sort", "play", "answer", "evaluate", "rank", "score", "grade"}
MAIL_VERBS = {"send", "post", "tweet", "retweet", "comment", "reply", "notify", "email", "mail", "sms", "dm", "mention", "chat",
              "greet", "broadcast", "publish", "share", "ping", "announce", "contact"}
PAY_VERBS = {"pay", "purchase", "buy", "sell", "book", "reserve", "place", "fund", "withdraw", "deposit", "trade", "checkout",
             "refund", "subscribe", "invest", "charge", "donate", "tip", "bid", "transfer", "wire", "remit", "spend"}
DELETE_VERBS = {"delete", "remove", "clear", "cancel", "revoke", "drop", "purge", "erase", "destroy", "unsubscribe", "unregister",
                "uninstall", "wipe", "truncate", "discard", "close"}
WRITE_VERBS = {"create", "update", "edit", "put", "insert", "register", "resolve", "archive", "replace", "follow", "unfollow",
               "login", "logout", "authenticate", "schedule", "reschedule", "log", "save", "modify", "change", "rename", "restore",
               "complete", "manage", "management", "assign", "submit", "upload", "mark", "move", "review", "configure",
               "reset", "write", "store", "sync", "import", "tag", "label", "pin", "like", "vote", "flag", "snooze", "dismiss",
               "accept", "reject", "approve", "deny", "confirm", "todo", "remind", "note", "enroll", "enrol", "signup", "sign",
               "join", "leave", "attach", "detach", "clone", "fork", "push", "commit", "merge", "deploy", "install", "provision",
               "migrate", "rollback", "increment", "decrement", "reorder", "set", "start", "stop", "activate", "deactivate",
               "adjust", "toggle", "enable", "disable", "lock", "unlock", "open", "turn", "switch", "increase", "decrease", "raise",
               "lower", "mute", "unmute", "pause", "resume", "restart", "reboot", "shutdown", "connect", "disconnect", "record",
               "checkin", "assign", "unassign", "block", "unblock", "invite", "kick", "ban", "grant", "apply"}
EXEC_VERBS = {"exec", "shell", "bash", "eval", "spawn", "subprocess", "sh", "cmd", "command", "script"}
CARRIER_VERBS = {"run", "execute", "perform", "do", "make", "handle", "process", "use", "invoke", "call", "trigger", "request",
                 "action"}
AMBIGUOUS_ADD = {"add", "append", "round", "total"}                 # arithmetic when a compute noun is around, else data.write
DEVICE_VERBS = {"press", "fill", "start", "stop", "lock", "unlock", "activate", "deactivate", "adjust", "set", "turn", "switch",
                "open", "close", "play", "pause", "resume", "skip", "connect", "disconnect", "preheat", "rotate", "dim", "brighten",
                "toggle", "control", "release", "engage", "disengage", "enable", "disable", "mute", "unmute", "increase", "decrease",
                "raise", "lower", "honk", "steer", "accelerate", "brake", "shift", "power", "reboot", "restart", "shutdown", "boot",
                "pair", "cast", "stream", "execute", "run", "operate", "drive", "park", "charge", "heat", "cool"}

# ---- nouns: for verb-less names, the CARRIER verbs, and the device family -----------------------
DEVICE_NOUNS = {"engine", "door", "doors", "brake", "brakes", "pedal", "fuel", "tank", "cruise", "climate", "headlight", "headlights",
                "light", "lights", "tire", "tires", "car", "vehicle", "appliance", "appliances", "oven", "thermostat", "bluetooth",
                "device", "devices", "speaker", "speakers", "volume", "media", "song", "songs", "music", "artist", "playlist",
                "spotify", "sound", "track", "album", "video", "movie", "tv", "ac", "heater", "fan", "window", "windows", "garage",
                "alarm", "camera", "lamp", "lamps", "gear", "wipers", "seat", "seats", "trunk", "hood", "radio", "printer", "navigation",
                "robot", "drone", "motor", "valve", "pump", "sensor", "screen", "wifi", "hotspot", "battery", "charger", "plug",
                "outlet", "blinds", "curtains", "sprinkler", "ignition", "horn", "parking", "led", "thinq", "smarthome", "iot",
                "hvac", "microwave", "fridge", "refrigerator", "dishwasher", "washer", "dryer", "vacuum", "doorbell", "lightbulb"}
COMPUTE_NOUNS = {"math", "factorial", "hypot", "mean", "median", "std", "variance", "logarithm", "sqrt", "percent", "percentage",
                 "probability", "prime", "gcd", "lcm", "area", "volume", "derivative", "integral", "permutation", "permutations",
                 "combination", "combinations", "binomial", "matrix", "quadratic", "equation", "equations", "roots", "circumference",
                 "perimeter", "velocity", "acceleration", "force", "wave", "frequency", "wavelength", "energy", "mass", "momentum",
                 "torque", "kinematics", "physics", "algebra", "geometry", "trigonometry", "calculus", "statistics", "regression",
                 "classifier", "ttest", "chi", "squared", "interest", "ratio", "ratios", "deviation", "odds", "winner", "valuation",
                 "depreciation", "growth", "evolution", "projection", "projections", "prediction", "predictions", "distance",
                 "mileage", "feasibility", "model", "models", "forest", "neural", "ml", "dna", "genome", "sentiment", "translation",
                 "translator", "melody", "chord", "sine", "cosine", "tangent", "magnetic", "electric", "electromagnetic", "gravity",
                 "gravitational", "thermodynamics", "entropy", "density", "bmi", "npv", "irr", "roi", "compound", "future", "value",
                 "sum", "average", "avg", "mode", "numbers", "number", "integers", "values", "digits", "string", "strings",
                 "analysis", "analyzer", "analyser", "generator", "generation", "simulation", "estimation", "conversion", "converter",
                 "calculator", "solver", "mixture", "recipe", "scale", "progression", "sequence", "pattern", "intersect", "zero",
                 "zeros", "trend", "trends", "revenue", "profit", "earnings", "repayment", "loan", "mortgage", "batting", "grossing",
                 "ppg", "performance", "impact", "potential", "identification", "recognition", "detection", "classification",
                 "regressor", "reasoning", "llm", "chart", "histogram", "graph", "image", "beat", "hypothesis", "random", "t",
                 "liter", "liters", "litre", "litres", "gallon", "gallons", "mile", "miles", "km", "kilometer", "kilometers", "kilometre",
                 "meter", "meters", "metre", "celsius", "fahrenheit", "kelvin", "pound", "pounds", "lb", "lbs", "kg", "kilogram",
                 "kilograms", "gram", "grams", "ounce", "ounces", "inch", "inches", "feet", "foot", "cm", "mm", "radians", "degrees",
                 "currency", "unit", "units", "vector", "vectors", "polynomial", "fraction", "fractions", "decimal", "hex", "binary"}
LOOKUP_NOUNS = {"weather", "temperature", "humidity", "precipitation", "air", "quality", "price", "prices", "rate", "rates", "quote",
                "quotes", "exchange", "stock", "stocks", "market", "news", "score", "scores", "ranking", "rankings", "rating",
                "ratings", "results", "result", "stats", "statistic", "history", "details", "detail", "info", "information", "status",
                "specs", "spec", "schedule", "timetable", "index", "data", "catalog", "directory", "help", "docs", "documentation",
                "faq", "version", "versions", "statement", "income", "gdp", "population", "forecast", "conditions", "condition",
                "attractions", "nearby", "closest", "nearest", "availability", "balance", "summary", "overview", "profile", "brief",
                "listing", "listings", "location", "coordinates", "timezone", "time", "date", "calendar", "events", "event", "matches",
                "match", "games", "game", "highest", "lowest", "latest", "current", "top", "count", "size", "length", "route",
                "routes", "routing", "directions", "eta", "products", "product", "items", "item", "prices", "menu", "hours", "address"}
EXEC_NOUNS = {"shell", "bash", "command", "cmd", "script", "terminal", "python", "code", "subprocess", "sh", "controller"}
PAY_NOUNS = {"payment", "payments", "order", "orders", "booking", "bookings", "reservation", "reservations", "purchase", "purchases",
             "ride", "rental", "invoice", "invoices", "checkout", "cart", "subscription", "wallet", "funds", "money", "transaction",
             "transactions", "insurance", "credit", "trade", "trades", "deposit", "withdrawal", "transfer", "refund", "bill", "bills",
             "fare", "fee", "fees", "premium", "card", "iban", "payout", "payroll", "salary"}      # "ticket" is NOT here: support tickets; the money verbs carry travel tickets
HARD_DELETE_VERBS = {"delete", "remove", "erase", "purge", "destroy", "wipe", "truncate", "revoke", "unsubscribe", "uninstall"}   # unambiguous wherever they sit
WRITE_NOUNS = {"todo", "todos", "reminder", "reminders", "note", "notes", "log", "entry", "entries", "settings", "config",
               "configuration", "preferences", "record", "records", "inventory", "watchlist", "playlist", "session", "project",
               "task", "tasks", "appointment", "appointments", "progress", "state", "flag", "flags", "tag", "tags", "label",
               "labels", "bookmark", "bookmarks", "alert", "alerts", "action"}
FILE_NOUNS = {"file", "files", "dir", "directory", "directories", "folder", "folders", "path", "paths", "filesystem", "fs"}
FS_WRITE_VERBS = {"write", "create", "save", "append", "edit", "update", "put", "upload", "touch", "mkdir", "move", "mv", "copy", "cp",
                  "rename", "transfer", "modify", "change", "add", "insert"}
FS_DELETE_VERBS = {"delete", "remove", "rm", "unlink", "erase", "purge", "wipe", "clear"}
WEB_FETCH_NOUNS = {"url", "urls", "http", "https", "download", "downloads", "crawl", "scrape", "webpage", "html"}
SEARCH_VERBS = {"search", "google", "bing", "duckduckgo", "duck", "browse"}
WEB_SEARCH_NOUNS = {"web", "engine", "internet", "online", "google", "bing", "duckduckgo", "duck", "browser"}

_VERB_FAMILY: dict[str, str] = {}
for _vs, _fam in ((READ_VERBS, "data.read"), (COMPUTE_VERBS, "compute.pure"), (MAIL_VERBS, "mail.send"), (PAY_VERBS, "payments.transfer"),
                  (DELETE_VERBS, "data.delete"), (WRITE_VERBS, "data.write"), (EXEC_VERBS, "code.exec"), (CARRIER_VERBS, "carrier"),
                  (AMBIGUOUS_ADD, "ambiguous")):
    for _v in _vs:
        _VERB_FAMILY.setdefault(_v, _fam)          # a verb listed in two sets keeps the FIRST (safer) family
del _vs, _fam, _v


def tokenize(name: str) -> str:
    """`Movies_3_FindMovies` -> "movies 3 find movies"; `pressBrakePedal` -> "press brake pedal";
    `weather.get` -> "weather get" — so verb/noun rules see word boundaries in any identifier style."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return re.sub(r"[_\.\-]+", " ", s).lower().strip()


def _segments(name: str) -> tuple[list[str], list[str]]:
    """(tokens of the LAST dotted segment, all tokens): `library.search_book` -> (["search","book"], ["library","search","book"])."""
    parts = [p for p in re.split(r"\.+", name) if p]
    return (tokenize(parts[-1]).split() if parts else []), tokenize(name).split()


def _res(scope: str, rule: str) -> dict:
    return {"scope": scope, "tier": _TIER[scope], "heuristic": True, "rule": rule}


def heuristic_resolve(name: str, description: str = "") -> dict | None:
    """Return {"scope", "tier", "heuristic": True, "rule": <deciding rule>} or None (unresolved, fail closed)."""
    if not name:
        return None
    last, toks = _segments(name)
    if not toks:
        return None
    tset = set(toks)
    # 0. the tool IS a shell command
    if len(toks) == 1 and toks[0] in SHELL:
        return _res(SHELL[toks[0]], "shell")
    # 1. file-system family by verb, when a file noun is present
    if tset & FILE_NOUNS:
        for t in toks:
            if t in FS_DELETE_VERBS: return _res("fs.delete", "file+delete-verb")
            if t in FS_WRITE_VERBS:  return _res("fs.write", "file+write-verb")
            if t in READ_VERBS:      return _res("fs.read", "file+read-verb")
    # 2. web egress families
    if tset & WEB_FETCH_NOUNS:
        return _res("web.fetch", "web-noun")
    if (tset & {"search", "browse"} and tset & WEB_SEARCH_NOUNS) or tset <= {"google", "bing", "duckduckgo", "duck", "go", "web", "search", "engine"}:
        return _res("web.search", "search+web-noun")                       # a search verb + a web noun, or a bare engine name
    # 3. hand-off / escalation to another agent or a human
    if tset & {"handover", "handoff", "escalate", "escalation"} or ("agent" in tset and tset & {"transfer", "hand", "route", "connect"}) \
            or ("support" in tset and tset & {"contact", "call", "request", "ask", "reach"}) or ("human" in tset and tset & {"agent", "operator", "transfer"}):
        return _res("agent.message", "handoff")
    # 3b. exports need curation (egress family that the closed vocabulary only has for CRM)
    if "export" in tset:
        return None
    # 4. the FIRST verb token decides (last dotted segment first, then the whole name); device verbs need a device noun
    verb, fam = None, None
    for seq in (last, toks):
        for t in seq:
            if t in DEVICE_VERBS and tset & DEVICE_NOUNS:
                return _res("device.actuate", "device-verb+noun")
            f = _VERB_FAMILY.get(t)
            if f is not None:
                verb, fam = t, f
                break
        if fam is not None:
            break
    if fam == "data.read":
        if verb in {"is", "has", "check", "verify", "validate", "get", "count"} and tset & COMPUTE_NOUNS and not tset & LOOKUP_NOUNS:
            return _res("compute.pure", "read-verb+compute-noun")            # is_prime, get_area — but find/search/lookup stay lookups
        return _res("data.read", "read-verb")                                # read verbs beat every noun (the T7 class fix)
    if tset & HARD_DELETE_VERBS:
        return _res("data.delete", "hard-delete-verb")                       # todo_delete: the destructive verb dominates wherever it sits
    if fam == "ambiguous":
        if tset & PAY_NOUNS: return _res("payments.transfer", "add+payment-noun")
        return _res("compute.pure" if (tset & COMPUTE_NOUNS and not tset & (WRITE_NOUNS | PAY_NOUNS | DEVICE_NOUNS)) else "data.write", "ambiguous-add")
    if fam == "data.write" and tset & PAY_NOUNS:
        return _res("payments.transfer", "write-verb+payment-noun")          # register_credit_card, update_order: a write on a payment instrument is tier 2
    if fam == "data.write" and verb in {"create", "make", "build"} and tset & COMPUTE_NOUNS and not tset & (WRITE_NOUNS | PAY_NOUNS | DEVICE_NOUNS | LOOKUP_NOUNS):
        return _res("compute.pure", "create+compute-noun")                   # create_histogram
    if fam in ("compute.pure", "mail.send", "payments.transfer", "data.delete", "data.write", "code.exec"):
        return _res(fam, "verb")
    # 5. carrier verb or no verb: the nouns decide — compute -> device -> exec -> payments -> write -> lookup
    if "test" in tset and tset & {"t", "chi", "hypothesis", "ttest", "squared", "sample"}: return _res("compute.pure", "noun:stat-test")
    if tset & COMPUTE_NOUNS and not tset & (PAY_NOUNS | EXEC_NOUNS): return _res("compute.pure", "noun:compute")
    if tset & DEVICE_NOUNS and tset & (DEVICE_VERBS | CARRIER_VERBS): return _res("device.actuate", "noun:device")
    if tset & EXEC_NOUNS: return _res("code.exec", "noun:exec")
    if tset & PAY_NOUNS: return _res("payments.transfer", "noun:payments")
    if tset & WRITE_NOUNS: return _res("data.write", "noun:write")
    if tset & LOOKUP_NOUNS: return _res("data.read", "noun:lookup")
    if fam == "carrier":
        if verb in {"run", "execute", "invoke"}: return _res("code.exec", "carrier-run")
        return _res("data.write", "carrier-write") if tset - CARRIER_VERBS else None
    if len(toks) == 3 and toks[1] == "to": return _res("compute.pure", "x-to-y")   # unit conversions: liter_to_gallon
    return None
