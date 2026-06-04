from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CustomerCheck, DashboardInsight, RoadmapItem, utcnow
from app.services.demo_matcher import DemoMatcher


ROADMAP_ITEMS = [
    ("SSO / SAML integration", "Enterprise authentication using SAML and Active Directory.", "In Progress", "Q3 2026", "High"),
    ("Custom dashboards", "Create dashboards with custom metrics and layouts.", "Planned", "Q4 2026", "High"),
    ("Audit logs", "Track user actions and administrative changes for compliance.", "Planned", "Q1 2027", "High"),
    ("Slack integration", "Send notifications and workflow updates directly to Slack.", "Planned", "Q4 2026", "Medium"),
    ("CSV / Excel export", "Export dashboard and reporting data to CSV or Excel.", "In Progress", "Q3 2026", "Medium"),
    ("Bulk user management", "Invite, update, and remove multiple users simultaneously.", "Planned", "Q1 2027", "Medium"),
    ("Mobile application", "Native mobile application for iOS and Android.", "Planned", "Q2 2027", "Low"),
]

REQUESTS = [
    ("Acme Corp", "Sarah Chen", "Need enterprise SSO with Active Directory before security approval", 48000, "Negotiation", True),
    ("TechCore", "Lee Morgan", "SAML login is required for our enterprise rollout", 62000, "Evaluation", True),
    ("BrightPath", "Tom Wells", "Our IT team requires single sign-on for all employees", 35000, "Evaluation", True),
    ("GrowthLabs", "Ana Lima", "Would love custom dashboards for each department", 25000, "Evaluation", False),
    ("Northstar Media", "Maya Patel", "Can every team create its own analytics dashboard", 18000, "Discovery", False),
    ("Titan Health", "Kevin Ross", "Audit logs are required for our compliance review", 90000, "Negotiation", True),
    ("Sentinel Finance", "Emma Dubois", "We need a full history of admin and user actions", 75000, "Evaluation", True),
    ("BloomAgency", "Mei Wang", "A Slack integration would remove a lot of manual work", 12000, "Discovery", False),
    ("ZenithCo", "Lars Beck", "Please add automatic AI meeting summaries", 55000, "Evaluation", False),
    ("Orbit Systems", "Daniel Kim", "We need AI-generated summaries after customer calls", 68000, "Negotiation", True),
    ("NordGroup", "James Eriksson", "The sales team needs a native mobile application", 48000, "Evaluation", False),
]


def seed_demo_data(db: Session) -> None:
    matcher = DemoMatcher()
    if not db.scalar(select(func.count(RoadmapItem.id))):
        for name, description, status, quarter, priority in ROADMAP_ITEMS:
            db.add(
                RoadmapItem(
                    name=name,
                    description=description,
                    status=status,
                    quarter=quarter,
                    priority=priority,
                    source="demo",
                )
            )
        db.commit()

    if not db.scalar(select(func.count(CustomerCheck.id))):
        roadmap = list(db.scalars(select(RoadmapItem).where(RoadmapItem.active.is_(True))))
        now = utcnow()
        for index, (company, contact, request, value, stage, blocker) in enumerate(REQUESTS):
            result = matcher.match(request, roadmap)
            created_at = now - timedelta(hours=index * 5)
            check = CustomerCheck(
                company=company,
                contact=contact,
                request_text=request,
                request_theme=result.request_theme,
                deal_value=value,
                deal_stage=stage,
                deal_blocker=blocker,
                source="demo",
                alignment=result.alignment,
                matched_roadmap_item_id=result.matched_roadmap_item_id,
                confidence=result.confidence,
                reasoning=result.reasoning,
                customer_note=result.customer_note,
                created_at=created_at,
            )
            if index in {3, 7}:
                check.customer_updated_at = created_at + timedelta(hours=2)
            if index in {0, 5, 8}:
                check.product_shared_at = created_at + timedelta(hours=1)
            if index == 7:
                check.resolved = True
                check.resolved_at = created_at + timedelta(hours=4)
            db.add(check)
        db.commit()

    if not db.scalar(select(func.count(DashboardInsight.id))):
        checks = list(db.scalars(select(CustomerCheck)))
        insight = matcher.dashboard_insight(checks)
        db.add(
            DashboardInsight(
                headline=insight.headline,
                summary=insight.summary,
                recommendation=insight.recommendation,
            )
        )
        db.commit()

