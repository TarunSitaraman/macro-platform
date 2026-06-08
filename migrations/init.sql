-- Hexaware Macro Platform — Bootstrap SQL
-- Run once against Neon PostgreSQL (pgvector must be available)

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- Seed indicator definitions (Phase 1 catalogue)
INSERT INTO indicator_definitions
  (indicator_code, indicator_name, category, standard_unit, description, frequency)
VALUES
  ('GDP_CURRENT_USD', 'GDP (Current USD)', 'Economic Growth', 'USD_BN',
   'Gross Domestic Product in current US Dollars (billions)', 'ANNUAL'),
  ('GDP_GROWTH', 'GDP Growth Rate', 'Economic Growth', 'PCT',
   'Annual percentage growth rate of GDP at market prices', 'ANNUAL'),
  ('CPI_INFLATION', 'CPI Inflation Rate', 'Inflation', 'PCT',
   'Consumer Price Index annual percentage change', 'ANNUAL'),
  ('UNEMPLOYMENT_RATE', 'Unemployment Rate', 'Employment', 'PCT',
   'Percentage of total labour force that is unemployed', 'ANNUAL'),
  ('CURRENT_ACCOUNT_PCT_GDP', 'Current Account Balance (% GDP)', 'Trade', 'PCT_GDP',
   'Current account balance as a percentage of GDP', 'ANNUAL'),
  ('GOVT_DEBT_PCT_GDP', 'Government Debt (% GDP)', 'Fiscal', 'PCT_GDP',
   'General government gross debt as a percentage of GDP', 'ANNUAL')
ON CONFLICT (indicator_code) DO NOTHING;

-- Seed source config (Phase 1 sources)
INSERT INTO source_config
  (source_code, source_name, source_url, source_type, frequency, reputation_score)
VALUES
  ('WORLD_BANK', 'World Bank Open Data', 'https://api.worldbank.org/v2',
   'API', 'MONTHLY', 95),
  ('IMF_WEO', 'IMF World Economic Outlook', 'https://www.imf.org/external/datamapper/api/v1',
   'API', 'QUARTERLY', 95),
  ('FRED', 'Federal Reserve Economic Data (FRED)', 'https://api.stlouisfed.org/fred',
   'API', 'MONTHLY', 90),
  ('OECD', 'OECD Statistics', 'https://sdmx.oecd.org/public/rest',
   'API', 'MONTHLY', 90),
  ('IMF_BLOG', 'IMF Blog', 'https://www.imf.org/en/Blogs',
   'HTML', 'WEEKLY', 85),
  ('WB_PROSPECTS', 'World Bank Global Economic Prospects',
   'https://www.worldbank.org/en/publication/global-economic-prospects',
   'HTML', 'QUARTERLY', 85)
ON CONFLICT (source_code) DO NOTHING;
