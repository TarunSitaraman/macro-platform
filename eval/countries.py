"""ISO3 country/aggregate codes -> human-readable display names.

Only the codes present in the gold layer are needed, plus a few common extras.
Falls back to the raw code when unknown so question text never breaks.
"""

COUNTRY_NAMES: dict[str, str] = {
    "ARG": "Argentina",
    "AUS": "Australia",
    "BRA": "Brazil",
    "CAN": "Canada",
    "CHN": "China",
    "DEU": "Germany",
    "ESP": "Spain",
    "FRA": "France",
    "GBR": "the United Kingdom",
    "IDN": "Indonesia",
    "IND": "India",
    "ITA": "Italy",
    "JPN": "Japan",
    "KOR": "South Korea",
    "LIC": "low-income countries",
    "MEX": "Mexico",
    "NLD": "the Netherlands",
    "SAU": "Saudi Arabia",
    "TUR": "Turkey",
    "USA": "the United States",
    "WLD": "the world",
    "ZAF": "South Africa",
}


def country_name(code: str) -> str:
    """Return a readable name for an ISO3 code, falling back to the code."""
    return COUNTRY_NAMES.get(code, code)


# How each standard unit reads inside a sentence / answer.
UNIT_PHRASE: dict[str, str] = {
    "PCT": "%",
    "PCT_GDP": "% of GDP",
    "USD_BN": "billion USD",
    "MILLIONS": "million",
}


def unit_phrase(unit: str | None) -> str:
    """Return a readable phrase for a standard unit code."""
    if not unit:
        return ""
    return UNIT_PHRASE.get(unit, unit)
