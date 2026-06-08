"""Chatbot REST endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agents.chatbot import ChatbotAgent, SUGGESTED_QUESTIONS
from src.agents.summarizer import SummarizerAgent
from src.database import get_db

router = APIRouter()


class MessageRequest(BaseModel):
    message: str


class SummaryRequest(BaseModel):
    country_code: str
    summary_type: str = "COUNTRY_SNAPSHOT"
    indicator_code: Optional[str] = None


@router.post("/chat/sessions")
async def create_session(db: Session = Depends(get_db)):
    agent = ChatbotAgent(db)
    sess = await agent.get_or_create_session()
    db.commit()
    return {
        "session_id": str(sess.session_id),
        "suggested_questions": SUGGESTED_QUESTIONS,
    }


@router.post("/chat/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageRequest,
    db: Session = Depends(get_db),
):
    agent = ChatbotAgent(db)
    result = await agent.chat(session_id=session_id, user_message=body.message)
    return result


@router.get("/chat/sessions/{session_id}/messages")
def get_history(session_id: str, db: Session = Depends(get_db)):
    agent = ChatbotAgent(db)
    return {"session_id": session_id, "messages": agent.get_history(session_id)}


@router.post("/summaries/generate")
async def generate_summary(body: SummaryRequest, db: Session = Depends(get_db)):
    agent = SummarizerAgent(db)
    valid_types = {"COUNTRY_SNAPSHOT", "INDICATOR_BRIEF", "SECTOR_ANALYSIS"}
    if body.summary_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"summary_type must be one of {valid_types}")

    if body.summary_type == "COUNTRY_SNAPSHOT":
        summary = await agent.generate_country_snapshot(body.country_code)
    elif body.summary_type == "INDICATOR_BRIEF":
        if not body.indicator_code:
            raise HTTPException(status_code=400, detail="indicator_code required for INDICATOR_BRIEF")
        summary = await agent.generate_indicator_brief(body.indicator_code)
    else:
        summary = await agent.generate_sector_analysis(body.country_code)

    return {
        "summary_id": str(summary.summary_id),
        "country_code": summary.country_code,
        "summary_type": summary.summary_type,
        "content": summary.content,
        "model_used": summary.model_used,
        "generated_at": summary.generated_at.isoformat(),
    }


@router.get("/summaries")
def list_summaries(
    country: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    agent = SummarizerAgent(db)
    rows = agent.list_summaries(country_code=country, summary_type=type, limit=limit)
    return [
        {
            "summary_id": str(r.summary_id),
            "country_code": r.country_code,
            "summary_type": r.summary_type,
            "content": r.content[:500] + "..." if len(r.content) > 500 else r.content,
            "generated_at": r.generated_at.isoformat(),
            "model_used": r.model_used,
        }
        for r in rows
    ]
