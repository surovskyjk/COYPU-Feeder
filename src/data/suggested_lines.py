"""
Curated list of well-known railway lines with verified OSM relation IDs.
Organised by country and corridor / line number.
"""

SUGGESTED_LINES: list[dict] = [
    # ── Czech Republic ────────────────────────────────────────────────────────
    {
        "country": "Czech Republic",
        "name": "Corridor I — Praha – Pardubice – Česká Třebová – Brno",
        "note": "Main Czech rail corridor (line 010/011)",
        "relation_id": 227036,
    },
    {
        "country": "Czech Republic",
        "name": "Corridor II — Praha – Kolín – Pardubice – Ostrava",
        "note": "Main Czech rail corridor (line 010/270)",
        "relation_id": 227035,
    },
    {
        "country": "Czech Republic",
        "name": "Corridor III — Cheb – Plzeň – Praha – Přerov – Ostrava",
        "note": "West–East transit corridor (line 170/320)",
        "relation_id": 227037,
    },
    {
        "country": "Czech Republic",
        "name": "Corridor IV — Praha – Tábor – České Budějovice",
        "note": "South Bohemian corridor (line 220/225)",
        "relation_id": 227038,
    },
    {
        "country": "Czech Republic",
        "name": "Praha – Děčín (line 090)",
        "note": "Northern Bohemia towards Germany",
        "relation_id": None,
        "search": "Praha Děčín",
    },
    {
        "country": "Czech Republic",
        "name": "Brno – Přerov – Bohumín (line 330)",
        "note": "Moravian corridor section",
        "relation_id": None,
        "search": "Brno Přerov",
    },
    {
        "country": "Czech Republic",
        "name": "Praha-Běchovice – Praha-Vyšehrad",
        "note": "Praha suburban segment (tested, ~35 km)",
        "relation_id": 3128446,
    },
    {
        "country": "Czech Republic",
        "name": "Praha-Libeň – Praha-Hostivař",
        "note": "Praha suburban freight/passenger ring",
        "relation_id": 8377165,
    },

    # ── Slovakia ──────────────────────────────────────────────────────────────
    {
        "country": "Slovakia",
        "name": "Bratislava – Žilina – Košice (main line)",
        "note": "Slovak main east–west railway",
        "relation_id": None,
        "search": "Bratislava Žilina",
    },
    {
        "country": "Slovakia",
        "name": "Bratislava – Kúty – Brno",
        "note": "Cross-border CZ–SK corridor",
        "relation_id": None,
        "search": "Bratislava Kúty",
    },

    # ── Austria ───────────────────────────────────────────────────────────────
    {
        "country": "Austria",
        "name": "Wien Hauptbahnhof – Salzburg (Westbahn)",
        "note": "Austrian main western corridor",
        "relation_id": None,
        "search": "Wien Salzburg Westbahn",
    },
    {
        "country": "Austria",
        "name": "Wien – Bratislava (Nordbahn)",
        "note": "Cross-border AT–SK line",
        "relation_id": None,
        "search": "Wien Bratislava Nordbahn",
    },

    # ── Germany ───────────────────────────────────────────────────────────────
    {
        "country": "Germany",
        "name": "München – Nürnberg (Schnellfahrstrecke)",
        "note": "German high-speed corridor",
        "relation_id": None,
        "search": "München Nürnberg",
    },
    {
        "country": "Germany",
        "name": "Dresden – Praha (Elbe Valley)",
        "note": "Cross-border DE–CZ scenic line",
        "relation_id": None,
        "search": "Dresden Praha",
    },

    # ── Poland ────────────────────────────────────────────────────────────────
    {
        "country": "Poland",
        "name": "Warszawa – Kraków (CMK corridor)",
        "note": "Polish main north–south line",
        "relation_id": None,
        "search": "Warszawa Kraków",
    },
    {
        "country": "Poland",
        "name": "Katowice – Ostrava – Bohumín",
        "note": "Cross-border PL–CZ freight corridor",
        "relation_id": None,
        "search": "Katowice Ostrava",
    },
]


def get_countries() -> list[str]:
    seen = []
    for line in SUGGESTED_LINES:
        c = line["country"]
        if c not in seen:
            seen.append(c)
    return seen


def get_lines_for_country(country: str) -> list[dict]:
    return [l for l in SUGGESTED_LINES if l["country"] == country]
