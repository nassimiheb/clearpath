import csv
import io


class FeedbackImportError(ValueError):
    pass


def parse_feedback_csv(content: bytes, limit: int = 25) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FeedbackImportError("CSV must use UTF-8 encoding.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "request" not in reader.fieldnames:
        raise FeedbackImportError("CSV must include a request column.")
    rows = []
    for line, row in enumerate(reader, start=2):
        request_text = (row.get("request") or "").strip()
        if not request_text:
            continue
        try:
            deal_value = int(float((row.get("deal_value") or "0").replace(",", "")))
        except ValueError as exc:
            raise FeedbackImportError(f"Invalid deal_value on CSV line {line}.") from exc
        rows.append(
            {
                "company": (row.get("company") or "").strip(),
                "contact": (row.get("contact") or "").strip(),
                "request_text": request_text,
                "deal_value": max(deal_value, 0),
                "deal_stage": (row.get("deal_stage") or "").strip(),
                "deal_blocker": (row.get("deal_blocker") or "").strip().lower()
                in {"yes", "true", "1", "y"},
            }
        )
        if len(rows) > limit:
            raise FeedbackImportError(f"CSV imports are limited to {limit} requests at a time.")
    if not rows:
        raise FeedbackImportError("CSV contains no customer requests.")
    return rows
