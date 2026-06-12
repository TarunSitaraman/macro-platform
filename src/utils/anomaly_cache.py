import json
import os
import uuid
import logging
from datetime import datetime, timezone, date
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from src.database import GoldRecord
from src.agents.forecaster import ForecasterAgent

logger = logging.getLogger(__name__)
CACHE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../temp/anomalies_cache.json"))

class AnomalyCacheManager:
    @staticmethod
    def _load_cache() -> Dict[str, Any]:
        if not os.path.exists(CACHE_FILE):
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            return {}
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load anomalies cache: %s", e)
            return {}

    @staticmethod
    def _save_cache(cache: Dict[str, Any]):
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            logger.error("Failed to save anomalies cache: %s", e)

    @classmethod
    def get_status(cls, tenant_id: Optional[uuid.UUID]) -> Dict[str, Any]:
        key = str(tenant_id) if tenant_id else "global"
        cache = cls._load_cache()
        t_cache = cache.get(key, {})
        return {
            "last_calculated_at": t_cache.get("last_calculated_at"),
            "is_calculating": t_cache.get("is_calculating", False),
            "anomalies_count": len(t_cache.get("anomalies", []))
        }

    @classmethod
    def get_anomalies(cls, tenant_id: Optional[uuid.UUID]) -> List[Dict[str, Any]]:
        key = str(tenant_id) if tenant_id else "global"
        cache = cls._load_cache()
        return cache.get(key, {}).get("anomalies", [])

    @classmethod
    def set_calculating(cls, tenant_id: Optional[uuid.UUID], is_calculating: bool):
        key = str(tenant_id) if tenant_id else "global"
        cache = cls._load_cache()
        if key not in cache:
            cache[key] = {"anomalies": [], "last_calculated_at": None}
        cache[key]["is_calculating"] = is_calculating
        cls._save_cache(cache)

    @classmethod
    def update_cache(cls, tenant_id: Optional[uuid.UUID], anomalies: List[Dict[str, Any]]):
        key = str(tenant_id) if tenant_id else "global"
        cache = cls._load_cache()
        cache[key] = {
            "anomalies": anomalies,
            "last_calculated_at": datetime.now(timezone.utc).isoformat(),
            "is_calculating": False
        }
        cls._save_cache(cache)

    @classmethod
    def calculate_and_cache(cls, db_session_maker, tenant_id: Optional[uuid.UUID]):
        """Run anomaly detection and write to cache."""
        key = str(tenant_id) if tenant_id else "global"
        logger.info("Starting background anomaly detection for tenant: %s", key)
        cls.set_calculating(tenant_id, True)

        db = db_session_maker()
        try:
            pairs = db.query(GoldRecord.indicator_code, GoldRecord.country_code).filter(
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id)
            ).distinct().all()

            agent = ForecasterAgent(db, tenant_id=tenant_id)
            all_anomalies = []

            today_str = date.today().isoformat()  # YYYY-MM-DD

            for ind, country in pairs:
                raw_records = db.query(GoldRecord).filter(
                    GoldRecord.indicator_code == ind,
                    GoldRecord.country_code == country,
                    (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id),
                    GoldRecord.is_forecast == False
                ).order_by(GoldRecord.period.asc()).all()

                # Deduplicate by period (keep highest DQ score per period) and
                # drop future-dated records (crawled forecasts stored as actuals).
                best_by_period: Dict[str, GoldRecord] = {}
                for r in raw_records:
                    # Normalise period to YYYY-MM-DD for date comparison
                    p = r.period
                    if len(p) == 4:
                        cmp = f"{p}-12-31"
                    elif "Q" in p:
                        yr, q = p.split("-Q")
                        month = int(q) * 3
                        cmp = f"{yr}-{month:02d}-28"
                    else:
                        cmp = p[:10]

                    if cmp > today_str:
                        continue  # skip future-dated records

                    existing = best_by_period.get(r.period)
                    if existing is None or (r.dq_score or 0) > (existing.dq_score or 0):
                        best_by_period[r.period] = r

                records = sorted(best_by_period.values(), key=lambda r: r.period)

                if len(records) < 10:
                    continue

                try:
                    anomalies = agent.detect_anomalies(records)
                    seen = set()
                    for a in anomalies:
                        ds_str = a["ds"].strftime("%Y-%m-%d") if isinstance(a["ds"], datetime) else str(a["ds"])
                        dedup_key = (ind, country, ds_str)
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                        act = float(a["actual"])
                        exp = float(a["expected"])
                        upper = float(a.get("upper", exp))
                        lower = float(a.get("lower", exp))
                        sigma = float(a.get("sigma", 0.0))
                        # Relative deviation: normalise by CI half-width to avoid
                        # explosion when expected ≈ 0 (e.g. flat CPI near zero).
                        ci_half = (upper - lower) / 2.0
                        if ci_half > 0:
                            deviation_pct = ((act - exp) / ci_half) * 100
                        elif exp != 0:
                            deviation_pct = ((act - exp) / abs(exp)) * 100
                        else:
                            deviation_pct = 0.0
                        all_anomalies.append({
                            "indicator_code": ind,
                            "country_code": country,
                            "date": ds_str,
                            "actual": act,
                            "expected": exp,
                            "deviation_pct": round(deviation_pct, 1),
                            "sigma": sigma,
                        })
                except Exception as ex:
                    logger.error("Error detecting anomalies for %s - %s: %s", ind, country, ex)

            all_anomalies.sort(key=lambda x: abs(x["deviation_pct"]), reverse=True)
            cls.update_cache(tenant_id, all_anomalies)
            logger.info("Background anomaly detection complete for tenant: %s. Found %d anomalies.", key, len(all_anomalies))
        except Exception as e:
            logger.error("Failed background anomaly calculation: %s", e)
            cls.set_calculating(tenant_id, False)
        finally:
            db.close()
