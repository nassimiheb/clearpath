import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import RoadmapItem


class NotionSyncError(RuntimeError):
    pass


class NotionService:
    api_url = "https://api.notion.com/v1"
    notion_version = "2025-09-03"

    def __init__(self, token: str, data_source_id: str, client: httpx.Client | None = None):
        self.token = token
        self.data_source_id = data_source_id
        self.client = client

    @staticmethod
    def _plain_text(values: list[dict]) -> str:
        return "".join(value.get("plain_text", "") for value in values)

    @classmethod
    def parse_page(cls, page: dict) -> dict:
        props = page.get("properties", {})
        required = {"Name", "Description", "Status", "Quarter", "Priority"}
        missing = required - props.keys()
        if missing:
            raise NotionSyncError(f"Notion database is missing columns: {', '.join(sorted(missing))}.")
        try:
            return {
                "notion_page_id": page["id"],
                "name": cls._plain_text(props["Name"]["title"]),
                "description": cls._plain_text(props["Description"]["rich_text"]),
                "status": (props["Status"]["status"] or {}).get("name", ""),
                "quarter": (props["Quarter"]["select"] or {}).get("name", ""),
                "priority": (props["Priority"]["select"] or {}).get("name", ""),
            }
        except (KeyError, TypeError) as exc:
            raise NotionSyncError("Notion columns exist but use unexpected property types.") from exc

    def fetch_pages(self) -> list[dict]:
        if not self.token or not self.data_source_id:
            raise NotionSyncError("NOTION_TOKEN and NOTION_DATA_SOURCE_ID must be configured.")
        headers = {"Authorization": f"Bearer {self.token}", "Notion-Version": self.notion_version}
        pages, cursor = [], None
        try:
            with self.client or httpx.Client(timeout=20) as client:
                while True:
                    body = {"page_size": 100}
                    if cursor:
                        body["start_cursor"] = cursor
                    response = client.post(
                        f"{self.api_url}/data_sources/{self.data_source_id}/query",
                        headers=headers,
                        json=body,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    pages.extend(payload.get("results", []))
                    if not payload.get("has_more"):
                        break
                    cursor = payload.get("next_cursor")
        except httpx.HTTPStatusError as exc:
            detail = exc.response.json().get("message", exc.response.text)
            raise NotionSyncError(f"Notion rejected the sync: {detail}") from exc
        except httpx.HTTPError as exc:
            raise NotionSyncError(f"Could not reach Notion: {exc}") from exc
        return pages

    def sync(self, db: Session) -> int:
        parsed = [self.parse_page(page) for page in self.fetch_pages()]
        seen = {item["notion_page_id"] for item in parsed}
        existing = {
            item.notion_page_id: item
            for item in db.scalars(select(RoadmapItem).where(RoadmapItem.source == "notion"))
        }
        for data in parsed:
            item = existing.get(data["notion_page_id"])
            if item:
                for key, value in data.items():
                    setattr(item, key, value)
                item.active = True
            else:
                db.add(RoadmapItem(**data, source="notion", active=True))
        if seen:
            db.execute(
                update(RoadmapItem)
                .where(RoadmapItem.source == "notion", RoadmapItem.notion_page_id.not_in(seen))
                .values(active=False)
            )
        else:
            db.execute(update(RoadmapItem).where(RoadmapItem.source == "notion").values(active=False))
        db.commit()
        return len(parsed)
