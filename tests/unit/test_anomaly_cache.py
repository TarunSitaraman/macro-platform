"""Unit tests for the anomaly detection caching system."""

import os
import json
import uuid
import pytest
from unittest.mock import MagicMock, patch
from src.utils.anomaly_cache import AnomalyCacheManager

def test_cache_manager_basic_get_set(tmp_path):
    # Use a temporary cache file to avoid polluting the workspace
    test_cache_file = os.path.join(tmp_path, "test_anomalies_cache.json")
    
    with patch("src.utils.anomaly_cache.CACHE_FILE", test_cache_file):
        tenant_id = uuid.uuid4()
        
        # Test default values when cache is empty/non-existent
        status = AnomalyCacheManager.get_status(tenant_id)
        assert status["last_calculated_at"] is None
        assert status["is_calculating"] is False
        assert status["anomalies_count"] == 0
        
        anomalies = AnomalyCacheManager.get_anomalies(tenant_id)
        assert anomalies == []
        
        # Test set_calculating
        AnomalyCacheManager.set_calculating(tenant_id, True)
        status = AnomalyCacheManager.get_status(tenant_id)
        assert status["is_calculating"] is True
        
        # Test update_cache
        test_anomalies = [
            {
                "indicator_code": "GDP_GROWTH",
                "country_code": "USA",
                "date": "2024-06-01",
                "actual": 3.5,
                "expected": 2.0,
                "deviation_pct": 75.0
            }
        ]
        AnomalyCacheManager.update_cache(tenant_id, test_anomalies)
        
        status = AnomalyCacheManager.get_status(tenant_id)
        assert status["is_calculating"] is False
        assert status["anomalies_count"] == 1
        assert status["last_calculated_at"] is not None
        
        anomalies = AnomalyCacheManager.get_anomalies(tenant_id)
        assert len(anomalies) == 1
        assert anomalies[0]["indicator_code"] == "GDP_GROWTH"

def test_calculate_and_cache(tmp_path):
    test_cache_file = os.path.join(tmp_path, "test_anomalies_cache.json")
    
    with patch("src.utils.anomaly_cache.CACHE_FILE", test_cache_file):
        tenant_id = uuid.uuid4()
        
        # Mock DB
        mock_db = MagicMock()
        mock_session_maker = MagicMock(return_value=mock_db)
        
        # Mock records: need at least 10 records
        mock_records = [MagicMock(period="2024-01") for _ in range(12)]
        
        # Use side_effect to route queries correctly based on arguments
        from src.database import GoldRecord
        def mock_query(*args):
            q = MagicMock()
            if len(args) == 2 and args[0] == GoldRecord.indicator_code and args[1] == GoldRecord.country_code:
                # pairs query
                q.filter.return_value.distinct.return_value.all.return_value = [
                    ("GDP_GROWTH", "USA")
                ]
            else:
                # records query
                q.filter.return_value.order_by.return_value.all.return_value = mock_records
            return q
            
        mock_db.query.side_effect = mock_query
        
        # Mock ForecasterAgent
        mock_agent_instance = MagicMock()
        mock_agent_instance.detect_anomalies.return_value = [
            {
                "ds": "2024-06-01",
                "actual": 3.5,
                "expected": 2.0
            }
        ]
        
        with patch("src.utils.anomaly_cache.ForecasterAgent", return_value=mock_agent_instance):
            AnomalyCacheManager.calculate_and_cache(mock_session_maker, tenant_id)
            
            # Assert cache was written
            anomalies = AnomalyCacheManager.get_anomalies(tenant_id)
            assert len(anomalies) == 1
            assert anomalies[0]["indicator_code"] == "GDP_GROWTH"
            assert anomalies[0]["country_code"] == "USA"
            assert anomalies[0]["actual"] == 3.5
            assert anomalies[0]["deviation_pct"] == 75.0
