"""Forecast tool — wraps ForecasterAgent."""

from uuid import UUID

from sqlalchemy.orm import Session

from src.agents.runtime.types import ToolResult


async def get_forecast(
    db: Session,
    tenant_id: UUID,
    indicator_code: str,
    country_code: str,
    periods: int = 4,
) -> ToolResult:
    """Generate Prophet forecast for an indicator/country pair."""
    from src.agents.forecaster import ForecasterAgent

    agent = ForecasterAgent(db, tenant_id=tenant_id)
    predictions = agent.run_forecast(
        indicator_code.upper(),
        country_code.upper(),
        periods=periods,
    )
    if not predictions:
        return ToolResult(
            tool_name="get_forecast",
            success=False,
            data=None,
            error="Insufficient historical data for forecast (need at least 5 points)",
        )
    return ToolResult(
        tool_name="get_forecast",
        success=True,
        data={"forecasts": predictions, "indicator_code": indicator_code, "country_code": country_code},
        record_ids=[],
    )
