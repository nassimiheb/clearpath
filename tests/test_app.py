from app.models import Alignment, CustomerCheck, DashboardInsight, RoadmapItem
from app.schemas import ClaudeDashboardInsight, ClaudeMatch
import time

from app.services.claude import ClaudeServiceError


class FakeMatcher:
    def match(self, request_text, roadmap_items):
        return ClaudeMatch(
            alignment=Alignment.on_roadmap,
            request_theme="Enterprise SSO",
            matched_roadmap_item_id=roadmap_items[0].id,
            confidence=96,
            reasoning="The request directly matches the SSO roadmap item.",
            customer_note="SSO is actively planned for Q2.",
        )

    def dashboard_insight(self, checks, variant=0):
        return ClaudeDashboardInsight(
            headline="Two SSO deals worth €80,000 need attention.",
            summary="Enterprise SSO is the strongest revenue signal.",
            recommendation="Prioritize the SSO launch and update blocked accounts.",
        )


class FailingMatcher:
    def match(self, request_text, roadmap_items):
        raise ClaudeServiceError("Claude is unavailable.")


def test_manual_roadmap_crud(client, db):
    response = client.post(
        "/roadmap",
        data={"name": "SSO", "description": "SAML login", "status": "Planned", "quarter": "Q2"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    item = db.query(RoadmapItem).one()
    assert item.name == "SSO"

    client.post(f"/roadmap/{item.id}/edit", data={"name": "Enterprise SSO", "priority": "High"})
    db.refresh(item)
    assert item.name == "Enterprise SSO"

    client.post(f"/roadmap/{item.id}/delete")
    assert db.query(RoadmapItem).count() == 0


def test_check_requires_roadmap(client):
    response = client.post("/checks", data={"request_text": "Need SSO"})
    assert response.status_code == 422
    assert "at least one roadmap item" in response.text


def test_long_running_forms_include_loading_states(client):
    check_page = client.get("/checks/new").text
    import_page = client.get("/feedback/import").text
    assert "ClearPath is checking the roadmap" in check_page
    assert "Import and run checks" in import_page
    assert 'id="loading-overlay"' in check_page


def test_successful_check_is_saved_and_visible(client, db):
    item = RoadmapItem(name="SSO / SAML", quarter="Q2 2026")
    db.add(item)
    db.commit()
    client.app.state.claude_matcher = FakeMatcher()

    response = client.post(
        "/checks",
        data={
            "company": "NordGroup",
            "contact": "James",
            "request_text": "Need Active Directory SSO",
            "deal_value": "48000",
            "deal_stage": "Negotiation",
            "deal_blocker": "true",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "On roadmap" in response.text
    assert db.query(CustomerCheck).count() == 1
    saved = db.query(CustomerCheck).one()
    assert saved.request_theme == "Enterprise SSO"
    assert saved.deal_value == 48000
    dashboard = client.get("/dashboard").text
    assert "NordGroup" in dashboard
    assert "Top priorities" in dashboard
    assert "€48,000" in dashboard


def test_failed_claude_call_is_not_saved(client, db):
    db.add(RoadmapItem(name="SSO"))
    db.commit()
    client.app.state.claude_matcher = FailingMatcher()
    response = client.post("/checks", data={"request_text": "Need SSO"})
    assert response.status_code == 502
    assert db.query(CustomerCheck).count() == 0


def test_csv_feedback_import_runs_bulk_checks(client, db):
    db.add(RoadmapItem(name="SSO"))
    db.commit()
    client.app.state.claude_matcher = FakeMatcher()
    csv_content = (
        "company,contact,request,deal_value,deal_stage,deal_blocker\n"
        "Acme,Sarah,Need SSO,32000,Evaluation,true\n"
        "TechCo,Lee,SAML login please,18000,Discovery,false\n"
    )
    response = client.post(
        "/feedback/import",
        files={"csv_file": ("feedback.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    progress_url = response.headers["location"]
    assert progress_url.startswith("/feedback/import/")
    progress_page = client.get(progress_url)
    assert "of <span id=\"progress-total\">2</span> checked" in progress_page.text
    for _ in range(30):
        status = client.get(f"{progress_url}/status").json()
        if status["status"] == "completed":
            break
        time.sleep(0.01)
    assert status["processed"] == 2
    assert status["succeeded"] == 2
    assert status["percent"] == 100
    assert db.query(CustomerCheck).count() == 2
    assert all(check.source == "csv" for check in db.query(CustomerCheck).all())


def test_csv_feedback_progress_reports_failures(client, db):
    db.add(RoadmapItem(name="SSO"))
    db.commit()
    client.app.state.claude_matcher = FailingMatcher()
    response = client.post(
        "/feedback/import",
        files={"csv_file": ("feedback.csv", "company,request\nAcme,Need SSO\n", "text/csv")},
        follow_redirects=False,
    )
    progress_url = response.headers["location"]
    for _ in range(30):
        status = client.get(f"{progress_url}/status").json()
        if status["status"] == "completed":
            break
        time.sleep(0.01)
    assert status["processed"] == 1
    assert status["succeeded"] == 0
    assert status["failed"] == 1
    assert "Acme: Claude is unavailable." in status["failures"]


def test_dashboard_insight_is_generated_and_persisted(client, db):
    db.add(
        CustomerCheck(
            company="Acme",
            request_text="Need SSO",
            request_theme="Enterprise SSO",
            alignment=Alignment.not_on_roadmap,
            confidence=90,
            reasoning="No match",
            customer_note="Not planned",
            deal_value=80000,
            deal_blocker=True,
        )
    )
    db.commit()
    client.app.state.claude_matcher = FakeMatcher()
    response = client.post("/dashboard/insight", follow_redirects=True)
    assert response.status_code == 200
    assert "Two SSO deals worth €80,000 need attention." in response.text
    assert "Insight refreshed" in response.text
    assert db.query(DashboardInsight).count() == 1


def test_request_actions_are_persisted(client, db):
    check = CustomerCheck(
        company="Acme",
        request_text="Need SSO",
        request_theme="Enterprise SSO",
        alignment=Alignment.on_roadmap,
        confidence=95,
        reasoning="Matched",
        customer_note="SSO is planned.",
    )
    db.add(check)
    db.commit()
    client.post(f"/checks/{check.id}/customer-updated")
    client.post(f"/checks/{check.id}/shared-with-product")
    client.post(f"/checks/{check.id}/resolve")
    db.refresh(check)
    assert check.customer_updated_at is not None
    assert check.product_shared_at is not None
    assert check.resolved is True
    assert check.resolved_at is not None
