import httpx
import re
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import CustomerCheck, RoadmapItem


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
    def normalize_name(name: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", name.lower()))

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
        all_items = list(db.scalars(select(RoadmapItem).order_by(RoadmapItem.id)))
        by_page_id = {
            item.notion_page_id: item
            for item in all_items
            if item.notion_page_id
        }
        by_name: dict[str, list[RoadmapItem]] = {}
        for item in all_items:
            by_name.setdefault(self.normalize_name(item.name), []).append(item)

        for data in parsed:
            page_item = by_page_id.get(data["notion_page_id"])
            name_matches = by_name.get(self.normalize_name(data["name"]), [])
            item = page_item or (name_matches[0] if name_matches else None)
            if not item:
                item = RoadmapItem(**data, source="notion", active=True)
                db.add(item)
                db.flush()
            else:
                for key, value in data.items():
                    setattr(item, key, value)
                item.source = "notion"
                item.active = True

            duplicates = [match for match in name_matches if match.id != item.id]
            if page_item and page_item.id != item.id:
                duplicates.append(page_item)
            for duplicate in {duplicate.id: duplicate for duplicate in duplicates}.values():
                db.execute(
                    update(CustomerCheck)
                    .where(CustomerCheck.matched_roadmap_item_id == duplicate.id)
                    .values(matched_roadmap_item_id=item.id)
                )
                db.delete(duplicate)

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
