"""News search tool — text and vector search over news_records."""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from src.agents.embeddings import EmbeddingError, embed_text
from src.agents.runtime.types import ToolResult
from src.database import NewsRecord

logger = logging.getLogger(__name__)


def _tenant_filter(tenant_id: UUID):
    return (NewsRecord.tenant_id == None) | (NewsRecord.tenant_id == tenant_id)


async def search_news(
    db: Session,
    tenant_id: UUID,
    query: str,
    limit: int = 5,
    country_code: Optional[str] = None,
) -> ToolResult:
    """Search recent macro news by semantic similarity or keyword."""
    records: list[NewsRecord] = []

    try:
        query_embedding = await embed_text(query)
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        sql = (
            "SELECT news_id FROM news_records "
            "WHERE embedding IS NOT NULL "
            "AND (tenant_id IS NULL OR tenant_id = :tenant_id) "
        )
        params: dict = {"emb": embedding_str, "lim": limit, "tenant_id": tenant_id}
        if country_code:
            sql += "AND country_code = :country "
            params["country"] = country_code.upper()
        sql += "ORDER BY embedding <-> CAST(:emb AS vector) LIMIT :lim"

        raw = db.execute(text(sql), params).fetchall()
        ids = [row[0] for row in raw]
        if ids:
            records = db.query(NewsRecord).filter(NewsRecord.news_id.in_(ids)).all()
    except EmbeddingError:
        pass
    except Exception as exc:
        logger.warning("News vector search failed: %s", exc)
        db.rollback()

    if not records:
        q = db.query(NewsRecord).filter(_tenant_filter(tenant_id))
        if country_code:
            q = q.filter(NewsRecord.country_code == country_code.upper())
        pattern = f"%{query}%"
        q = q.filter(
            or_(
                NewsRecord.title.ilike(pattern),
                NewsRecord.content.ilike(pattern),
            )
        )
        records = q.order_by(NewsRecord.published_at.desc().nullslast()).limit(limit).all()

    data = [
        {
            "news_id": str(r.news_id),
            "title": r.title,
            "source_name": r.source_name,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "sentiment_label": r.sentiment_label,
            "country_code": r.country_code,
            "snippet": (r.content or "")[:300],
            "url": r.url,
        }
        for r in records
    ]
    return ToolResult(
        tool_name="search_news",
        success=True,
        data={"articles": data},
        record_ids=[],
    )
