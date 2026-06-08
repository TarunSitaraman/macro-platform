"""Review Queue — human-in-the-loop approval interface."""

import asyncio
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.agents.pipeline import Pipeline
from src.database import ReviewQueue, SessionLocal, SilverRecord, SourceConfig

st.title("👁️ Review Queue")
st.caption("Human-in-the-loop review for records with DQ score 70–90%")

# ── Queue stats ─────────────────────────────────────────────────────────────────
db = SessionLocal()
try:
    from sqlalchemy import func
    pending = db.query(func.count(ReviewQueue.queue_id)).filter(ReviewQueue.status == "PENDING").scalar() or 0
    approved = db.query(func.count(ReviewQueue.queue_id)).filter(ReviewQueue.status.in_(["APPROVED", "ADJUSTED"])).scalar() or 0
    rejected_count = db.query(func.count(ReviewQueue.queue_id)).filter(ReviewQueue.status == "REJECTED").scalar() or 0
    sla_breached = (
        db.query(func.count(ReviewQueue.queue_id))
        .filter(ReviewQueue.status == "PENDING", ReviewQueue.sla_deadline < datetime.now(timezone.utc))
        .scalar() or 0
    )
finally:
    db.close()

c1, c2, c3, c4 = st.columns(4)
c1.metric("⏳ Pending", pending)
c2.metric("✅ Approved/Adjusted", approved)
c3.metric("❌ Rejected", rejected_count)
c4.metric("🚨 SLA Breached", sla_breached, delta_color="inverse")

st.divider()

# ── Load pending items ──────────────────────────────────────────────────────────
reviewer_name = st.text_input("Reviewer Name", value="analyst", key="reviewer_name")

@st.cache_data(ttl=30)
def load_pending():
    db = SessionLocal()
    try:
        rows = (
            db.query(ReviewQueue)
            .filter(ReviewQueue.status == "PENDING")
            .order_by(ReviewQueue.sla_deadline)
            .limit(50)
            .all()
        )
        return [
            {
                "queue_id": str(r.queue_id),
                "silver_id": str(r.silver_id),
                "Indicator": r.indicator_code,
                "Country": r.country_code,
                "Period": r.period,
                "Extracted Value": r.extracted_value,
                "DQ Score": r.dq_score,
                "dq_breakdown": r.dq_breakdown or {},
                "failure_reasons": r.failure_reasons or [],
                "Source URL": r.source_url,
                "SLA Deadline": r.sla_deadline.strftime("%Y-%m-%d %H:%M UTC") if r.sla_deadline else "",
                "SLA Breached": r.sla_deadline < datetime.now(timezone.utc) if r.sla_deadline else False,
            }
            for r in rows
        ]
    finally:
        db.close()


items = load_pending()

if not items:
    st.success("✅ No pending review items. The queue is clear.")
    st.stop()

st.subheader(f"Pending Items ({len(items)})")

for item in items:
    sla_color = "🚨" if item["SLA Breached"] else "⏰"
    with st.expander(
        f"{sla_color} {item['Indicator']} | {item['Country']} | {item['Period']} — DQ: {item['DQ Score']:.1f}%"
    ):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Extracted Value:** `{item['Extracted Value']}`")
            st.markdown(f"**SLA Deadline:** {item['SLA Deadline']}")
            if item["Source URL"]:
                st.markdown(f"**Source:** [{item['Source URL']}]({item['Source URL']})")

        with col2:
            st.markdown("**DQ Breakdown:**")
            breakdown = item["dq_breakdown"]
            if breakdown:
                for k, v in breakdown.items():
                    st.progress(v / 100, text=f"{k.title()}: {v:.1f}%")

        if item["failure_reasons"]:
            st.warning(f"**Failure reasons:** {', '.join(item['failure_reasons'])}")

        st.markdown("---")
        action = st.radio(
            "Action",
            ["Approve", "Adjust Value", "Reject"],
            key=f"action_{item['queue_id']}",
            horizontal=True,
        )

        adjusted = None
        if action == "Adjust Value":
            adjusted = st.number_input(
                "Corrected Value",
                value=float(item["Extracted Value"]) if item["Extracted Value"].replace(".", "").replace("-", "").isdigit() else 0.0,
                key=f"adj_{item['queue_id']}",
            )

        notes = st.text_input("Review Notes (optional)", key=f"notes_{item['queue_id']}")

        if st.button("Submit Decision", key=f"submit_{item['queue_id']}"):
            if not reviewer_name.strip():
                st.error("Please enter reviewer name above")
            else:
                db = SessionLocal()
                try:
                    q_item = db.query(ReviewQueue).filter(ReviewQueue.queue_id == item["queue_id"]).first()
                    silver = db.query(SilverRecord).filter(SilverRecord.record_id == item["silver_id"]).first()

                    if action == "Reject":
                        q_item.status = "REJECTED"
                        q_item.reviewed_by = reviewer_name
                        q_item.reviewed_at = datetime.now(timezone.utc)
                        q_item.review_notes = notes
                        db.commit()
                        st.success("Record rejected.")
                    else:
                        if adjusted is not None:
                            silver.value = adjusted
                            q_item.adjusted_value = adjusted
                            q_item.status = "ADJUSTED"
                        else:
                            q_item.status = "APPROVED"
                        q_item.reviewed_by = reviewer_name
                        q_item.reviewed_at = datetime.now(timezone.utc)
                        q_item.review_notes = notes
                        db.flush()

                        src = db.query(SourceConfig).filter(SourceConfig.source_code == silver.source_code).first()
                        pipeline = Pipeline(db)
                        gold = asyncio.run(pipeline.promote_to_gold(
                            silver=silver,
                            source_name=src.source_name if src else silver.source_code,
                            source_url=q_item.source_url or "",
                            crawled_at=silver.processed_at or datetime.now(timezone.utc),
                            approved_by=reviewer_name,
                        ))
                        db.commit()
                        st.success(f"✅ Promoted to Gold! Record ID: {gold.record_id}")
                finally:
                    db.close()
                st.cache_data.clear()
                st.rerun()
