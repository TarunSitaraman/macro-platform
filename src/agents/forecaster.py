"""Forecasting agent using Prophet to predict future macroeconomic trends."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List

import pandas as pd
from prophet import Prophet
from sqlalchemy.orm import Session

from src.database import GoldRecord
from src.config import INDICATOR_CATALOGUE

# Silence cmdstanpy logging
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

class ForecasterAgent:
    """Handles time-series forecasting and anomaly detection."""

    def __init__(self, db: Session, tenant_id: Optional[uuid.UUID] = None):
        self.db = db
        self.tenant_id = tenant_id

    def _prepare_data(self, records: List[GoldRecord]) -> pd.DataFrame:
        """Convert GoldRecords to Prophet-ready DataFrame (ds, y)."""
        if not records:
            return pd.DataFrame()
            
        data = []
        for r in records:
            # Handle different period formats (YYYY, YYYY-MM, YYYY-QN)
            # Prophet expects standard date strings
            ds = r.period
            if len(ds) == 4: # YYYY
                ds = f"{ds}-01-01"
            elif "Q" in ds: # YYYY-QN
                year, q = ds.split("-Q")
                month = (int(q) - 1) * 3 + 1
                ds = f"{year}-{month:02d}-01"
            
            data.append({"ds": ds, "y": r.value})
            
        df = pd.DataFrame(data)
        df['ds'] = pd.to_datetime(df['ds'])
        return df.sort_values('ds')

    def run_forecast(
        self, 
        indicator_code: str, 
        country_code: str, 
        periods: int = 4
    ) -> List[dict]:
        """Generate forecast for the next N periods."""
        # Fetch historical data (excluding existing forecasts)
        historical = (
            self.db.query(GoldRecord)
            .filter(
                GoldRecord.indicator_code == indicator_code,
                GoldRecord.country_code == country_code,
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id),
                GoldRecord.is_forecast == False
            )
            .order_by(GoldRecord.period.asc())
            .all()
        )
        
        if len(historical) < 5:
            logger.warning("Not enough data for %s/%s to forecast", indicator_code, country_code)
            return []

        df = self._prepare_data(historical)
        
        # Determine frequency
        freq = INDICATOR_CATALOGUE.get(indicator_code, {}).get("frequency", "ANNUAL")
        p_freq = 'YE' if freq == 'ANNUAL' else 'QE' if freq == 'QUARTERLY' else 'ME'
        
        try:
            m = Prophet(
                interval_width=0.95, # 95% confidence interval
                yearly_seasonality=True if freq != 'ANNUAL' else False,
                weekly_seasonality=False,
                daily_seasonality=False
            )
            m.fit(df)
            
            future = m.make_future_dataframe(periods=periods, freq=p_freq)
            forecast = m.predict(future)
            
            # Extract only the future points
            predicted = forecast.tail(periods)
            
            results = []
            for _, row in predicted.iterrows():
                # Convert back to period string
                dt = row['ds']
                if freq == 'ANNUAL':
                    period_str = str(dt.year)
                elif freq == 'QUARTERLY':
                    q = (dt.month - 1) // 3 + 1
                    period_str = f"{dt.year}-Q{q}"
                else:
                    period_str = f"{dt.year}-{dt.month:02d}"
                
                results.append({
                    "period": period_str,
                    "value": float(row['yhat']),
                    "upper": float(row['yhat_upper']),
                    "lower": float(row['yhat_lower']),
                })
            return results
        except Exception as e:
            logger.error("Forecast failed for %s/%s: %s", indicator_code, country_code, e)
            return []

    def detect_anomalies(self, records: List[GoldRecord]) -> List[dict]:
        """Detect if recent points deviate from the trend."""
        # Basic implementation: use the last 10% of data as 'test' points
        if len(records) < 10:
            return []
            
        df = self._prepare_data(records)
        train_size = int(len(df) * 0.9)
        train_df = df.iloc[:train_size]
        test_df = df.iloc[train_size:]
        
        try:
            m = Prophet(interval_width=0.99) # Higher threshold for anomalies
            m.fit(train_df)
            forecast = m.predict(test_df)
            
            anomalies = []
            for idx, row in test_df.iterrows():
                f_row = forecast[forecast['ds'] == row['ds']].iloc[0]
                if row['y'] > f_row['yhat_upper'] or row['y'] < f_row['yhat_lower']:
                    anomalies.append({
                        "ds": row['ds'],
                        "actual": row['y'],
                        "expected": f_row['yhat'],
                        "upper": f_row['yhat_upper'],
                        "lower": f_row['yhat_lower'],
                    })
            return anomalies
        except Exception as e:
            logger.error("Anomaly detection failed: %s", e)
            return []
