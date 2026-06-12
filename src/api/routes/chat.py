"""Chatbot REST endpoints."""

import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agents.chatbot import ChatbotAgent, SUGGESTED_QUESTIONS
from src.agents.summarizer import SummarizerAgent
from src.agents.researcher import ResearcherAgent
from src.utils.reporting import generate_pdf_report
from src.database import get_db, User
from src.utils.auth import get_current_user

router = APIRouter()


class MessageRequest(BaseModel):
    message: str


class SummaryRequest(BaseModel):
    country_code: str
    summary_type: str = "COUNTRY_SNAPSHOT"
    indicator_code: Optional[str] = None


@router.post("/chat/sessions")
async def create_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    agent = ChatbotAgent(db, tenant_id=current_user.tenant_id, user_id=current_user.user_id)
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
    current_user: User = Depends(get_current_user)
):
    agent = ChatbotAgent(db, tenant_id=current_user.tenant_id, user_id=current_user.user_id)
    result = await agent.chat(session_id=session_id, user_message=body.message)
    return result


@router.get("/chat/sessions/{session_id}/messages")
def get_history(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    agent = ChatbotAgent(db, tenant_id=current_user.tenant_id, user_id=current_user.user_id)
    return {"session_id": session_id, "messages": agent.get_history(session_id)}


@router.post("/summaries/generate")
async def generate_summary(
    body: SummaryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    agent = SummarizerAgent(db, tenant_id=current_user.tenant_id)
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
    current_user: User = Depends(get_current_user)
):
    agent = SummarizerAgent(db, tenant_id=current_user.tenant_id)
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


# ── Researcher endpoints ──

class ResearchRequest(BaseModel):
    topic: str


@router.post("/researcher/compile")
async def compile_research_report(
    body: ResearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Trigger Lead Researcher agent to gather web/internal data and generate report."""
    try:
        agent = ResearcherAgent(db, tenant_id=current_user.tenant_id)
        report = await agent.compile_report(body.topic)
        
        # Filename encodes owner identity so the download endpoint can verify it
        owner_token = str(current_user.user_id).replace("-", "")
        pdf_filename = f"research_{owner_token}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}.pdf"
        os.makedirs("temp", exist_ok=True)
        pdf_path = os.path.join("temp", pdf_filename)
        generate_pdf_report(body.topic, report["content"], pdf_path)
        
        report["pdf_filename"] = pdf_filename
        return report
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Failed compiling report: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/researcher/download-pdf")
def download_research_pdf(
    filename: str,
    current_user: User = Depends(get_current_user)
):
    """Serve compiled research report PDF file."""
    clean_filename = os.path.basename(filename)
    if not clean_filename.startswith("research_") or not clean_filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid filename format")

    # Verify ownership: filename is research_<owner_id_no_dashes>_<timestamp>_<nonce>.pdf
    parts = clean_filename[len("research_"):-len(".pdf")].split("_")
    if len(parts) < 3:
        raise HTTPException(status_code=403, detail="Access denied")
    owner_token = parts[0]
    expected_token = str(current_user.user_id).replace("-", "")
    if owner_token != expected_token:
        raise HTTPException(status_code=403, detail="Access denied")

    pdf_path = os.path.join("temp", clean_filename)
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="Research report not found")

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=clean_filename
    )
