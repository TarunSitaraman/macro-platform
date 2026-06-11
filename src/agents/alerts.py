"""Agent for monitoring macroeconomic signals and triggering alerts."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict

from sqlalchemy.orm import Session
from src.database import GoldRecord, AuditLog
from src.config import INDICATOR_CATALOGUE

logger = logging.getLogger(__name__)

class AlertAgent:
    """Monitors the Gold layer for user-defined or system-critical conditions."""

    def __init__(self, db: Session, tenant_id: Optional[uuid.UUID] = None):
        self.db = db
        self.tenant_id = tenant_id

    def check_thresholds(self) -> List[Dict]:
        """Check recently promoted records against standard macro thresholds."""
        # Focus on records promoted in the last 24 hours
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        
        recent_gold = (
            self.db.query(GoldRecord)
            .filter(
                GoldRecord.promoted_at >= cutoff,
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id),
                GoldRecord.is_forecast == False
            )
            .all()
        )
        
        signals = []
        for r in recent_gold:
            # Logic: Alert if Inflation > 5% or GDP Growth < 0 (Recession risk)
            if r.indicator_code == "CPI_INFLATION" and r.value > 5.0:
                signals.append({
                    "type": "CRITICAL",
                    "msg": f"High Inflation Alert: {r.country_code} CPI is {r.value}%",
                    "indicator": r.indicator_code,
                    "record_id": str(r.record_id)
                })
            elif r.indicator_code == "GDP_GROWTH" and r.value < 0:
                signals.append({
                    "type": "WARNING",
                    "msg": f"Negative Growth: {r.country_code} GDP growth is {r.value}%",
                    "indicator": r.indicator_code,
                    "record_id": str(r.record_id)
                })
                
        # Log signals to AuditLog for persistence in the UI
        for s in signals:
            self.db.add(AuditLog(
                tenant_id=self.tenant_id,
                table_name="gold_records",
                record_id=uuid.UUID(s["record_id"]),
                action="UPDATE", # Using UPDATE as a proxy for 'Signal Triggered'
                new_values=s,
                actor="AlertAgent",
                actor_role="system",
                reason=s["msg"]
            ))
        
        self.db.commit()
        return signals
