import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from app.migrations import migrate_database
from app.models import Alignment, CustomerCheck, RoadmapItem
from app.schemas import ClaudeDashboardInsight, ClaudeMatch
from app.services.claude import ClaudeMatcher
from app.services.demo_matcher import DemoMatcher
from app.services.imports import FeedbackImportError, parse_feedback_csv
from app.services.notion import NotionService, NotionSyncError


def notion_page(page_id="page-1", name="SSO"):
    return {
        "id": page_id,
        "properties": {
            "Name": {"title": [{"plain_text": name}]},
            "Description": {"rich_text": [{"plain_text": "SAML support"}]},
            "Status": {"status": {"name": "In Progress"}},
            "Quarter": {"select": {"name": "Q2 2026"}},
            "Priority": {"select": {"name": "High"}},
        },
    }


def test_prompt_contains_request_and_roadmap():
    prompt = ClaudeMatcher.build_prompt("Need SSO", [RoadmapItem(id=7, name="SAML login")])
    assert "Need SSO" in prompt
    assert "SAML login" in prompt
    assert '"id": 7' in prompt


def test_claude_output_schema_forbids_additional_properties():
    schema = ClaudeMatch.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "minimum" not in schema["properties"]["confidence"]
    assert "maximum" not in schema["properties"]["confidence"]
    assert ClaudeDashboardInsight.model_json_schema()["additionalProperties"] is False


def test_claude_output_still_validates_confidence_range():
    with pytest.raises(ValidationError):
        ClaudeMatch(
            alignment="on_roadmap",
            request_theme="SSO",
            matched_roadmap_item_id=1,
            confidence=101,
            reasoning="Match",
            customer_note="Planned",
        )


def test_feedback_csv_parser_reads_deal_context():
    rows = parse_feedback_csv(
        b"company,request,deal_value,deal_stage,deal_blocker\nAcme,Need SSO,48,Negotiation,yes\n"
    )
    assert rows[0]["deal_value"] == 48
    assert rows[0]["deal_blocker"] is True


def test_feedback_csv_requires_request_column():
    with pytest.raises(FeedbackImportError):
        parse_feedback_csv(b"company,feedback\nAcme,Need SSO\n")


def test_existing_customer_checks_table_is_migrated():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE customer_checks ("
                "id INTEGER PRIMARY KEY, company VARCHAR(240), request_text TEXT)"
            )
        )
    migrate_database(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("customer_checks")}
    assert {
        "request_theme",
        "deal_value",
        "deal_stage",
        "deal_blocker",
        "source",
        "resolved",
        "resolved_at",
        "customer_updated_at",
        "product_shared_at",
    } <= columns


def test_dashboard_prompt_contains_revenue_risk_data():
    check = CustomerCheck(
        company="Acme",
        request_text="Need SSO",
        request_theme="Enterprise SSO",
        alignment=Alignment.not_on_roadmap,
        confidence=90,
        reasoning="No match",
        customer_note="Not planned",
        deal_value=48000,
        deal_blocker=True,
    )
    prompt = ClaudeMatcher.build_dashboard_prompt([check])
    assert "Enterprise SSO" in prompt
    assert "48000" in prompt
    assert '"deal_blocker": true' in prompt


def test_demo_matcher_handles_direct_and_uncovered_requests():
    matcher = DemoMatcher()
    roadmap = [
        RoadmapItem(id=1, name="SSO / SAML integration", description="Enterprise login"),
        RoadmapItem(id=2, name="Audit logs", description="Compliance history"),
    ]
    direct = matcher.match("Our IT team requires SAML single sign-on", roadmap)
    uncovered = matcher.match("Please add automatic AI meeting summaries", roadmap)
    assert direct.alignment == Alignment.on_roadmap
    assert direct.matched_roadmap_item_id == 1
    assert direct.request_theme == "Enterprise SSO"
    assert uncovered.alignment == Alignment.not_on_roadmap
    assert uncovered.request_theme == "AI summaries"


def test_demo_matcher_generates_dashboard_insight_without_api():
    insight = DemoMatcher().dashboard_insight(
        [
            CustomerCheck(
                company="Acme",
                request_text="Need SSO",
                request_theme="Enterprise SSO",
                alignment=Alignment.not_on_roadmap,
                confidence=88,
                reasoning="No match",
                customer_note="Recorded",
                deal_value=48000,
                deal_blocker=True,
            )
        ]
    )
    assert "Enterprise SSO" in insight.headline
    assert "€48,000" in insight.headline


def test_notion_schema_validation():
    page = notion_page()
    del page["properties"]["Priority"]
    try:
        NotionService.parse_page(page)
        assert False, "Expected schema error"
    except NotionSyncError as exc:
        assert "Priority" in str(exc)


def test_notion_pagination_and_sync(db):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"results": [notion_page()], "has_more": True, "next_cursor": "next"})
        return httpx.Response(200, json={"results": [notion_page("page-2", "Audit logs")], "has_more": False})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = NotionService("token", "source", client)
    assert service.sync(db) == 2
    assert db.query(RoadmapItem).count() == 2
    assert calls == 2


def test_notion_deactivates_missing_rows_but_not_manual(db):
    notion = RoadmapItem(name="Old", source="notion", notion_page_id="old")
    manual = RoadmapItem(name="Manual", source="manual")
    db.add_all([notion, manual])
    db.commit()
    service = NotionService("token", "source")
    service.fetch_pages = lambda: []
    assert service.sync(db) == 0
    db.refresh(notion)
    db.refresh(manual)
    assert notion.active is False
    assert manual.active is True
