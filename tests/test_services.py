import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from app.migrations import migrate_database
from app.models import Alignment, CustomerCheck, RoadmapItem
from app.schemas import ClaudeDashboardInsight, ClaudeMatch
from app.services.claude import ClaudeMatcher
from app.services.demo_matcher import DemoMatcher
from app.services.demo_seed import seed_demo_data
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


def test_demo_matcher_rotates_dashboard_insight_themes():
    checks = [
        CustomerCheck(
            request_text="Need SSO",
            request_theme="Enterprise SSO",
            alignment=Alignment.not_on_roadmap,
            confidence=88,
            reasoning="No match",
            customer_note="Recorded",
            deal_value=48000,
            deal_blocker=True,
        ),
        CustomerCheck(
            request_text="Need AI summaries",
            request_theme="AI summaries",
            alignment=Alignment.not_on_roadmap,
            confidence=88,
            reasoning="No match",
            customer_note="Recorded",
            deal_value=30000,
        ),
    ]
    first = DemoMatcher().dashboard_insight(checks, variant=0)
    second = DemoMatcher().dashboard_insight(checks, variant=1)
    assert first.headline != second.headline


def test_demo_seed_populates_dynamic_data_and_is_idempotent(db):
    seed_demo_data(db)
    roadmap_count = db.query(RoadmapItem).count()
    check_count = db.query(CustomerCheck).count()
    insight_count = db.execute(text("select count(*) from dashboard_insights")).scalar_one()
    assert roadmap_count == 7
    assert check_count == 11
    assert insight_count == 1
    assert db.query(CustomerCheck).filter(CustomerCheck.deal_blocker.is_(True)).count() >= 1
    assert db.query(CustomerCheck).filter(CustomerCheck.resolved.is_(True)).count() == 1
    assert db.query(CustomerCheck).filter(CustomerCheck.request_theme == "AI summaries").count() == 2

    seed_demo_data(db)
    assert db.query(RoadmapItem).count() == roadmap_count
    assert db.query(CustomerCheck).count() == check_count
    assert db.execute(text("select count(*) from dashboard_insights")).scalar_one() == insight_count


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


def test_notion_sync_skips_and_removes_nameless_notion_rows(db):
    blank = RoadmapItem(name="", source="notion", notion_page_id="old-blank")
    db.add(blank)
    db.commit()
    check = CustomerCheck(
        request_text="Unknown request",
        alignment=Alignment.partial,
        matched_roadmap_item_id=blank.id,
        confidence=50,
        reasoning="Old match",
        customer_note="Reviewing",
    )
    db.add(check)
    db.commit()
    service = NotionService("token", "source")
    service.fetch_pages = lambda: [notion_page(page_id="new-blank", name="   ")]

    assert service.sync(db) == 0
    db.refresh(check)
    assert db.query(RoadmapItem).count() == 0
    assert check.matched_roadmap_item_id is None


def test_notion_sync_reuses_demo_item_with_same_normalized_name(db):
    demo = RoadmapItem(name="SSO / SAML integration", source="demo", quarter="Q1 2027")
    db.add(demo)
    db.commit()
    service = NotionService("token", "source")
    service.fetch_pages = lambda: [notion_page(name="SSO SAML Integration")]

    assert service.sync(db) == 1
    items = db.query(RoadmapItem).all()
    assert len(items) == 1
    assert items[0].id == demo.id
    assert items[0].source == "notion"
    assert items[0].notion_page_id == "page-1"
    assert items[0].quarter == "Q2 2026"


def test_notion_sync_merges_existing_duplicates_and_preserves_check_match(db):
    demo = RoadmapItem(name="Audit logs", source="demo")
    notion = RoadmapItem(name="AUDIT LOGS", source="notion", notion_page_id="old-page")
    db.add_all([demo, notion])
    db.commit()
    check = CustomerCheck(
        request_text="Need audit logs",
        alignment=Alignment.on_roadmap,
        matched_roadmap_item_id=demo.id,
        confidence=90,
        reasoning="Matched",
        customer_note="Planned",
    )
    db.add(check)
    db.commit()
    service = NotionService("token", "source")
    service.fetch_pages = lambda: [notion_page(page_id="old-page", name="Audit logs")]

    service.sync(db)
    db.refresh(check)
    items = db.query(RoadmapItem).all()
    assert len(items) == 1
    assert items[0].id == notion.id
    assert check.matched_roadmap_item_id == notion.id


def test_notion_sync_does_not_duplicate_seeded_demo_roadmap(db):
    seed_demo_data(db)
    assert db.query(RoadmapItem).count() == 7
    service = NotionService("token", "source")
    service.fetch_pages = lambda: [notion_page(name="SSO SAML Integration")]

    service.sync(db)
    assert db.query(RoadmapItem).count() == 7
    synced = db.query(RoadmapItem).filter(RoadmapItem.notion_page_id == "page-1").one()
    assert synced.source == "notion"
