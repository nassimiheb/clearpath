from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db
from app.migrations import migrate_database
from app.models import Alignment, CustomerCheck, DashboardInsight, RoadmapItem, utcnow
from app.services.claude import ClaudeMatcher, ClaudeServiceError
from app.services.imports import FeedbackImportError, parse_feedback_csv
from app.services.import_jobs import ImportJob, ImportJobManager
from app.services.notion import NotionService, NotionSyncError

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=ROOT / "templates")


def alignment_label(value: Alignment | str) -> str:
    labels = {
        Alignment.on_roadmap: "On roadmap",
        Alignment.partial: "Partially covered",
        Alignment.not_on_roadmap: "Not on roadmap",
    }
    try:
        return labels[Alignment(value)]
    except ValueError:
        return str(value)


templates.env.globals["alignment_label"] = alignment_label
templates.env.globals["format_deal_value"] = lambda value: f"€{value:,.0f}" if value else "—"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    migrate_database(engine)
    yield


def active_roadmap(db: Session) -> list[RoadmapItem]:
    return list(
        db.scalars(select(RoadmapItem).where(RoadmapItem.active.is_(True)).order_by(RoadmapItem.id))
    )


def save_check(db: Session, result, data: dict, source: str = "manual") -> CustomerCheck:
    check = CustomerCheck(
        company=data.get("company", "").strip(),
        contact=data.get("contact", "").strip(),
        request_text=data["request_text"].strip(),
        request_theme=result.request_theme.strip(),
        deal_value=data.get("deal_value", 0),
        deal_stage=data.get("deal_stage", "").strip(),
        deal_blocker=data.get("deal_blocker", False),
        source=source,
        alignment=result.alignment,
        matched_roadmap_item_id=result.matched_roadmap_item_id,
        confidence=result.confidence,
        reasoning=result.reasoning,
        customer_note=result.customer_note,
    )
    db.add(check)
    db.commit()
    return check


def build_opportunities(checks: list[CustomerCheck]) -> list[dict]:
    groups = defaultdict(list)
    for check in checks:
        theme = check.request_theme.strip() or (
            check.matched_roadmap_item.name if check.matched_roadmap_item else "Other requests"
        )
        groups[theme].append(check)
    opportunities = []
    for theme, grouped in groups.items():
        companies = sorted({check.company for check in grouped if check.company})
        alignments = Counter(check.alignment.value for check in grouped)
        opportunities.append(
            {
                "theme": theme,
                "checks": grouped,
                "request_count": len(grouped),
                "companies": companies,
                "deal_value": sum(check.deal_value for check in grouped),
                "blockers": sum(check.deal_blocker for check in grouped),
                "uncovered": alignments[Alignment.not_on_roadmap.value],
            }
        )
    return sorted(
        opportunities,
        key=lambda group: (group["blockers"], group["deal_value"], group["request_count"]),
        reverse=True,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.state.claude_matcher = ClaudeMatcher(settings.anthropic_api_key, settings.anthropic_model)
    app.state.notion_service = NotionService(settings.notion_token, settings.notion_data_source_id)
    app.state.import_jobs = ImportJobManager()
    app.state.session_factory = SessionLocal

    @app.get("/")
    def home():
        return RedirectResponse("/checks/new")

    @app.get("/checks/new")
    def new_check(request: Request, db: Session = Depends(get_db)):
        items = active_roadmap(db)
        return templates.TemplateResponse(
            request, "check_form.html", {"items": items, "error": request.query_params.get("error")}
        )

    @app.post("/checks")
    def create_check(
        request: Request,
        company: str = Form(""),
        contact: str = Form(""),
        request_text: str = Form(...),
        deal_value: int = Form(0),
        deal_stage: str = Form(""),
        deal_blocker: bool = Form(False),
        db: Session = Depends(get_db),
    ):
        items = active_roadmap(db)
        data = {
            "company": company,
            "contact": contact,
            "request_text": request_text,
            "deal_value": max(deal_value, 0),
            "deal_stage": deal_stage,
            "deal_blocker": deal_blocker,
        }
        context = {"items": items, **data}
        if not request_text.strip():
            context["error"] = "Customer request is required."
            return templates.TemplateResponse(request, "check_form.html", context, status_code=422)
        if not items:
            context["error"] = "Add or sync at least one roadmap item before running a check."
            return templates.TemplateResponse(request, "check_form.html", context, status_code=422)
        try:
            result = request.app.state.claude_matcher.match(request_text.strip(), items)
        except ClaudeServiceError as exc:
            context["error"] = str(exc)
            return templates.TemplateResponse(request, "check_form.html", context, status_code=502)

        check = save_check(db, result, data)
        return RedirectResponse(f"/checks/{check.id}", status_code=303)

    @app.get("/checks/{check_id}")
    def check_detail(request: Request, check_id: int, db: Session = Depends(get_db)):
        check = db.get(CustomerCheck, check_id)
        if not check:
            raise HTTPException(404, "Check not found")
        similar = db.scalars(
            select(CustomerCheck)
            .where(CustomerCheck.request_theme == check.request_theme, CustomerCheck.id != check.id)
            .order_by(CustomerCheck.created_at.desc())
        ).all()
        return templates.TemplateResponse(
            request,
            "check_detail.html",
            {
                "check": check,
                "similar": similar,
                "message": request.query_params.get("message"),
            },
        )

    @app.post("/checks/{check_id}/resolve")
    def resolve_check(check_id: int, db: Session = Depends(get_db)):
        check = db.get(CustomerCheck, check_id)
        if not check:
            raise HTTPException(404, "Check not found")
        check.resolved = not check.resolved
        check.resolved_at = utcnow() if check.resolved else None
        db.commit()
        message = "Request+marked+resolved" if check.resolved else "Request+reopened"
        return RedirectResponse(f"/checks/{check_id}?message={message}", status_code=303)

    @app.post("/checks/{check_id}/customer-updated")
    def mark_customer_updated(check_id: int, db: Session = Depends(get_db)):
        check = db.get(CustomerCheck, check_id)
        if not check:
            raise HTTPException(404, "Check not found")
        check.customer_updated_at = utcnow()
        db.commit()
        return RedirectResponse(f"/checks/{check_id}?message=Customer+update+recorded", status_code=303)

    @app.post("/checks/{check_id}/shared-with-product")
    def mark_shared_with_product(check_id: int, db: Session = Depends(get_db)):
        check = db.get(CustomerCheck, check_id)
        if not check:
            raise HTTPException(404, "Check not found")
        check.product_shared_at = utcnow()
        db.commit()
        return RedirectResponse(f"/checks/{check_id}?message=Product+share+recorded", status_code=303)

    @app.get("/feedback/import")
    def feedback_import(request: Request):
        return templates.TemplateResponse(request, "feedback_import.html", {})

    @app.post("/feedback/import")
    async def import_feedback(
        request: Request,
        csv_file: UploadFile = File(...),
        db: Session = Depends(get_db),
    ):
        items = active_roadmap(db)
        if not items:
            return templates.TemplateResponse(
                request,
                "feedback_import.html",
                {"error": "Add or sync at least one roadmap item before importing feedback."},
                status_code=422,
            )
        try:
            rows = parse_feedback_csv(await csv_file.read())
        except FeedbackImportError as exc:
            return templates.TemplateResponse(
                request, "feedback_import.html", {"error": str(exc)}, status_code=422
            )
        job = request.app.state.import_jobs.create(len(rows))
        matcher = request.app.state.claude_matcher
        session_factory = request.app.state.session_factory

        def run_import(import_job: ImportJob) -> None:
            with import_job.lock:
                import_job.status = "running"
            with session_factory() as worker_db:
                worker_items = active_roadmap(worker_db)
                for row in rows:
                    company = row["company"] or "Unknown company"
                    with import_job.lock:
                        import_job.current_company = company
                    try:
                        result = matcher.match(row["request_text"], worker_items)
                        check = save_check(worker_db, result, row, source="csv")
                        with import_job.lock:
                            import_job.succeeded += 1
                            import_job.result_ids.append(check.id)
                    except Exception as exc:
                        worker_db.rollback()
                        with import_job.lock:
                            import_job.failed += 1
                            import_job.failures.append(f"{company}: {exc}")
                    finally:
                        with import_job.lock:
                            import_job.processed += 1
                with import_job.lock:
                    import_job.current_company = ""
                    import_job.status = "completed"

        request.app.state.import_jobs.start(job, run_import)
        return RedirectResponse(f"/feedback/import/{job.id}", status_code=303)

    @app.get("/feedback/import/{job_id}")
    def feedback_import_progress(request: Request, job_id: str):
        job = request.app.state.import_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Import job not found")
        return templates.TemplateResponse(request, "feedback_progress.html", {"job": job.snapshot()})

    @app.get("/feedback/import/{job_id}/status")
    def feedback_import_status(request: Request, job_id: str):
        job = request.app.state.import_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Import job not found")
        return JSONResponse(job.snapshot())

    @app.get("/roadmap")
    def roadmap(request: Request, db: Session = Depends(get_db)):
        items = db.scalars(select(RoadmapItem).order_by(RoadmapItem.active.desc(), RoadmapItem.name)).all()
        return templates.TemplateResponse(
            request,
            "roadmap.html",
            {
                "items": items,
                "message": request.query_params.get("message"),
                "error": request.query_params.get("error"),
            },
        )

    @app.post("/roadmap")
    def add_roadmap_item(
        name: str = Form(...),
        description: str = Form(""),
        status: str = Form("Planned"),
        quarter: str = Form(""),
        priority: str = Form("Medium"),
        db: Session = Depends(get_db),
    ):
        if not name.strip():
            return RedirectResponse("/roadmap?error=Name+is+required", status_code=303)
        db.add(
            RoadmapItem(
                name=name.strip(),
                description=description.strip(),
                status=status.strip(),
                quarter=quarter.strip(),
                priority=priority.strip(),
            )
        )
        db.commit()
        return RedirectResponse("/roadmap?message=Roadmap+item+added", status_code=303)

    @app.get("/roadmap/{item_id}/edit")
    def edit_roadmap_form(request: Request, item_id: int, db: Session = Depends(get_db)):
        item = db.get(RoadmapItem, item_id)
        if not item or item.source != "manual":
            raise HTTPException(404, "Editable roadmap item not found")
        return templates.TemplateResponse(request, "roadmap_edit.html", {"item": item})

    @app.post("/roadmap/{item_id}/edit")
    def edit_roadmap_item(
        item_id: int,
        name: str = Form(...),
        description: str = Form(""),
        status: str = Form("Planned"),
        quarter: str = Form(""),
        priority: str = Form("Medium"),
        db: Session = Depends(get_db),
    ):
        item = db.get(RoadmapItem, item_id)
        if not item or item.source != "manual":
            raise HTTPException(404, "Editable roadmap item not found")
        item.name, item.description = name.strip(), description.strip()
        item.status, item.quarter, item.priority = status.strip(), quarter.strip(), priority.strip()
        db.commit()
        return RedirectResponse("/roadmap?message=Roadmap+item+updated", status_code=303)

    @app.post("/roadmap/{item_id}/delete")
    def delete_roadmap_item(item_id: int, db: Session = Depends(get_db)):
        item = db.get(RoadmapItem, item_id)
        if not item or item.source != "manual":
            raise HTTPException(404, "Deletable roadmap item not found")
        db.delete(item)
        db.commit()
        return RedirectResponse("/roadmap?message=Roadmap+item+deleted", status_code=303)

    @app.post("/roadmap/sync-notion")
    def sync_notion(request: Request, db: Session = Depends(get_db)):
        try:
            count = request.app.state.notion_service.sync(db)
        except NotionSyncError as exc:
            return templates.TemplateResponse(
                request,
                "sync_result.html",
                {"error": str(exc)},
                status_code=502,
            )
        return templates.TemplateResponse(
            request, "sync_result.html", {"message": f"Synced {count} roadmap items from Notion."}
        )

    @app.get("/dashboard")
    def dashboard(request: Request, db: Session = Depends(get_db)):
        checks = db.scalars(select(CustomerCheck).order_by(CustomerCheck.created_at.desc())).all()
        counts = Counter(check.alignment.value for check in checks)
        insight = db.scalar(select(DashboardInsight).order_by(DashboardInsight.generated_at.desc()))
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "checks": checks,
                "counts": counts,
                "total": len(checks),
                "deal_value": sum(check.deal_value for check in checks),
                "blockers": sum(check.deal_blocker for check in checks),
                "opportunities": build_opportunities(checks),
                "insight": insight,
                "error": request.query_params.get("error"),
            },
        )

    @app.post("/dashboard/insight")
    def generate_dashboard_insight(request: Request, db: Session = Depends(get_db)):
        checks = list(db.scalars(select(CustomerCheck).order_by(CustomerCheck.created_at.desc())))
        try:
            result = request.app.state.claude_matcher.dashboard_insight(checks)
        except ClaudeServiceError as exc:
            return RedirectResponse(f"/dashboard?error={quote_plus(str(exc))}", status_code=303)
        db.add(
            DashboardInsight(
                headline=result.headline.strip(),
                summary=result.summary.strip(),
                recommendation=result.recommendation.strip(),
            )
        )
        db.commit()
        return RedirectResponse("/dashboard", status_code=303)

    return app


app = create_app()
