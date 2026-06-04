import re
from collections import Counter, defaultdict

from app.models import Alignment, CustomerCheck, RoadmapItem
from app.schemas import ClaudeDashboardInsight, ClaudeMatch
from app.services.claude import ClaudeServiceError


STOP_WORDS = {
    "a", "all", "an", "and", "are", "as", "at", "be", "before", "can", "for", "from",
    "get", "in", "is", "it", "need", "our", "please", "the", "their", "to", "we", "with",
}
THEMES = {
    "Enterprise SSO": {"sso", "saml", "single", "sign-on", "active", "directory"},
    "Custom dashboards": {"dashboard", "dashboards", "analytics", "report", "reports", "reporting"},
    "Audit logs": {"audit", "logs", "history", "compliance", "actions"},
    "Slack integration": {"slack"},
    "AI summaries": {"ai", "summary", "summaries", "meeting", "calls"},
    "Mobile application": {"mobile", "ios", "android", "phones", "application", "app"},
    "Data export": {"csv", "excel", "export", "download"},
    "Bulk user management": {"bulk", "users", "user", "management", "invite"},
}


def words(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9-]+", text.lower())
        if token not in STOP_WORDS and len(token) > 1
    }


def normalize_theme(request_text: str) -> str:
    request_words = words(request_text)
    theme, score = max(
        ((name, len(request_words & keywords)) for name, keywords in THEMES.items()),
        key=lambda item: item[1],
    )
    if score:
        return theme
    meaningful = sorted(request_words)[:4]
    return " ".join(word.title() for word in meaningful) or "Other requests"


class DemoMatcher:
    def match(self, request_text: str, roadmap_items: list[RoadmapItem]) -> ClaudeMatch:
        request_words = words(request_text)
        request_theme = normalize_theme(request_text)
        scored = []
        for item in roadmap_items:
            item_words = words(f"{item.name} {item.description}")
            overlap = len(request_words & item_words)
            coverage = overlap / max(min(len(request_words), 6), 1)
            if request_theme in THEMES and normalize_theme(f"{item.name} {item.description}") == request_theme:
                overlap = max(overlap, 3)
                coverage = max(coverage, 0.75)
            scored.append((coverage, overlap, item))
        coverage, overlap, matched = max(scored, key=lambda result: (result[0], result[1]))
        if overlap >= 2 and coverage >= 0.30:
            alignment, confidence = Alignment.on_roadmap, min(96, 70 + overlap * 6)
        elif overlap >= 1:
            alignment, confidence = Alignment.partial, min(78, 48 + overlap * 8)
        else:
            alignment, confidence, matched = Alignment.not_on_roadmap, 88, None

        theme = request_theme
        if matched:
            eta = f" with an expected delivery of {matched.quarter}" if matched.quarter else ""
            reasoning = (
                f"Demo matching found related roadmap terms in '{matched.name}'. "
                f"This is classified as {alignment.value.replace('_', ' ')}."
            )
            customer_note = (
                f"We found a related roadmap item: {matched.name}{eta}. "
                "We will keep you updated as planning progresses."
            )
        else:
            reasoning = "Demo matching found no meaningful overlap with the active roadmap."
            customer_note = (
                "This capability is not currently represented on our roadmap. "
                "We have recorded the request for product review."
            )
        return ClaudeMatch(
            alignment=alignment,
            request_theme=theme,
            matched_roadmap_item_id=matched.id if matched else None,
            confidence=confidence,
            reasoning=reasoning,
            customer_note=customer_note,
        )

    def dashboard_insight(self, checks: list[CustomerCheck]) -> ClaudeDashboardInsight:
        if not checks:
            raise ClaudeServiceError("Run or import at least one request before generating insight.")
        groups = defaultdict(list)
        for check in checks:
            groups[check.request_theme or "Other requests"].append(check)
        ranked = sorted(
            groups.items(),
            key=lambda item: (
                sum(check.deal_blocker for check in item[1]),
                sum(check.deal_value for check in item[1]),
                len(item[1]),
            ),
            reverse=True,
        )
        theme, group = ranked[0]
        blockers = sum(check.deal_blocker for check in group)
        value = sum(check.deal_value for check in group)
        uncovered = Counter(check.alignment for check in group)[Alignment.not_on_roadmap]
        request_label = "request" if len(group) == 1 else "requests"
        blocker_label = "deal blocker" if blockers == 1 else "deal blockers"
        uncovered_label = "request" if uncovered == 1 else "requests"
        all_request_label = "request" if len(checks) == 1 else "requests"
        headline = (
            f"{theme} leads demand with {len(group)} {request_label} and €{value:,.0f} in tracked value."
        )
        summary = (
            f"This theme includes {blockers} {blocker_label} and {uncovered} {uncovered_label} not "
            f"covered by the roadmap. Across all data, ClearPath is tracking {len(checks)} customer "
            f"{all_request_label}."
        )
        recommendation = (
            f"Review {theme} with product and prioritize updates to the highest-value blocked accounts."
        )
        return ClaudeDashboardInsight(
            headline=headline,
            summary=summary,
            recommendation=recommendation,
        )
