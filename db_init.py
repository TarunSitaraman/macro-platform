# -*- coding: utf-8 -*-
"""
One-shot database initialiser.
Run once after filling in .env:

    python db_init.py
"""

import sys
from dotenv import load_dotenv

load_dotenv()

from src.database import init_db, SessionLocal, SourceConfig, IndicatorDefinition
from src.config import INDICATOR_CATALOGUE


def seed_indicators(db):
    for code, meta in INDICATOR_CATALOGUE.items():
        existing = db.query(IndicatorDefinition).get(code)
        if not existing:
            db.add(IndicatorDefinition(
                indicator_code=code,
                indicator_name=meta["name"],
                category=meta["category"],
                standard_unit=meta["standard_unit"],
                description=meta["description"],
                frequency=meta["frequency"],
            ))
    db.commit()
    print(f"  [OK] Seeded {len(INDICATOR_CATALOGUE)} indicators")


def seed_sources(db):
    sources = [
        dict(source_code="WORLD_BANK", source_name="World Bank Open Data",
             source_url="https://api.worldbank.org/v2", source_type="API",
             frequency="MONTHLY", reputation_score=95),
        dict(source_code="IMF_WEO", source_name="IMF World Economic Outlook",
             source_url="https://www.imf.org/external/datamapper/api/v1",
             source_type="API", frequency="QUARTERLY", reputation_score=95),
        dict(source_code="FRED", source_name="Federal Reserve Economic Data",
             source_url="https://api.stlouisfed.org/fred",
             source_type="API", frequency="MONTHLY", reputation_score=90),
        dict(source_code="OECD", source_name="OECD Statistics",
             source_url="https://sdmx.oecd.org/public/rest",
             source_type="API", frequency="MONTHLY", reputation_score=90),
        dict(source_code="IMF_BLOG", source_name="IMF Blog",
             source_url="https://www.imf.org/en/Blogs",
             source_type="HTML", frequency="WEEKLY", reputation_score=85,
             extraction_prompt="Extract macroeconomic indicator values — GDP growth rates, inflation, unemployment — for any country mentioned. Include exact numeric values, the year/period, and whether figures are actual or forecast."),
        dict(source_code="WB_PROSPECTS", source_name="World Bank Global Economic Prospects",
             source_url="https://www.worldbank.org/en/publication/global-economic-prospects",
             source_type="HTML", frequency="QUARTERLY", reputation_score=85,
             extraction_prompt="Extract GDP growth forecasts, inflation forecasts, and economic outlook figures for all countries or regions mentioned. Include year and whether values are actual or forecast."),
        dict(source_code="OECD_OUTLOOK", source_name="OECD Economic Outlook",
             source_url="https://www.oecd.org/en/topics/economic-outlook-analysis-and-forecasts.html",
             source_type="HTML", frequency="QUARTERLY", reputation_score=88,
             extraction_prompt="Extract GDP growth, inflation, unemployment, and fiscal balance figures for OECD member countries. Note whether figures are projections or actuals. Include year."),
        dict(source_code="BIS_REVIEW", source_name="BIS Quarterly Review",
             source_url="https://www.bis.org/publ/qtrpdf/r_qt2412.htm",
             source_type="HTML", frequency="QUARTERLY", reputation_score=87,
             extraction_prompt="Extract macroeconomic figures including GDP growth, inflation rates, current account balances, and government debt ratios mentioned for any country. Include the time period."),
    ]
    added = 0
    for s in sources:
        existing = db.query(SourceConfig).filter(SourceConfig.source_code == s["source_code"]).first()
        if not existing:
            prompt = s.pop("extraction_prompt", None)
            row = SourceConfig(**s)
            row.extraction_prompt = prompt
            db.add(row)
            added += 1
    db.commit()
    print(f"  [OK] Seeded {added} sources ({len(sources) - added} already existed)")


if __name__ == "__main__":
    print("Initialising Macro Intelligence Platform database...")
    print("  Creating tables and enabling pgvector...")
    try:
        init_db()
        print("  [OK] Schema created")
    except Exception as e:
        print(f"  [FAIL] Schema creation failed: {e}")
        sys.exit(1)

    db = SessionLocal()
    try:
        seed_indicators(db)
        seed_sources(db)
    finally:
        db.close()

    print("")
    print("Database ready. Run the app with:")
    print("   streamlit run src\\ui\\app.py")
