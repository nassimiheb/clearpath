import json

from anthropic import Anthropic

from app.models import CustomerCheck, RoadmapItem
from app.schemas import ClaudeDashboardInsight, ClaudeMatch


class ClaudeServiceError(RuntimeError):
    pass


class ClaudeMatcher:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @staticmethod
    def build_prompt(request_text: str, roadmap_items: list[RoadmapItem]) -> str:
        roadmap = [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "status": item.status,
                "quarter": item.quarter,
                "priority": item.priority,
            }
            for item in roadmap_items
        ]
        return (
            "Compare the customer request with the roadmap. Choose on_roadmap only for a "
            "clear direct match, partial for a related item that does not fully satisfy it, "
            "and not_on_roadmap when nothing meaningfully matches. The matched ID must be "
            "one of the supplied IDs, or null. Set request_theme to a short normalized feature "
            "name that groups semantically similar requests, such as 'Enterprise SSO'. "
            "Be concise and do not promise delivery beyond "
            "the roadmap data.\n\n"
            f"Customer request:\n{request_text}\n\nRoadmap:\n{json.dumps(roadmap)}"
        )

    def match(self, request_text: str, roadmap_items: list[RoadmapItem]) -> ClaudeMatch:
        if not self.api_key:
            raise ClaudeServiceError("ANTHROPIC_API_KEY is not configured.")
        try:
            client = Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=800,
                system="You are a careful product-roadmap analyst for a customer success team.",
                messages=[{"role": "user", "content": self.build_prompt(request_text, roadmap_items)}],
                extra_body={
                    "output_config": {
                        "format": {
                            "type": "json_schema",
                            "schema": ClaudeMatch.model_json_schema(),
                        }
                    }
                },
            )
            result = ClaudeMatch.model_validate_json(response.content[0].text)
        except ClaudeServiceError:
            raise
        except Exception as exc:
            raise ClaudeServiceError(f"Claude request failed: {exc}") from exc

        valid_ids = {item.id for item in roadmap_items}
        if result.matched_roadmap_item_id not in valid_ids:
            result.matched_roadmap_item_id = None
        return result

    @staticmethod
    def build_dashboard_prompt(checks: list[CustomerCheck]) -> str:
        demand = [
            {
                "company": check.company,
                "theme": check.request_theme,
                "request": check.request_text,
                "alignment": check.alignment.value,
                "deal_value_eur": check.deal_value,
                "deal_stage": check.deal_stage,
                "deal_blocker": check.deal_blocker,
                "resolved": check.resolved,
            }
            for check in checks
        ]
        return (
            "Analyze this customer-demand dataset for product and customer-success leaders. "
            "Identify demand patterns, roadmap gaps, and revenue risks. Use exact counts and "
            "euro values from the data. The headline should be one strong sentence. The summary "
            "should explain the most important evidence in 2-3 sentences. The recommendation "
            "should give one concrete next action. Do not invent facts.\n\n"
            f"Customer demand:\n{json.dumps(demand)}"
        )

    def dashboard_insight(
        self, checks: list[CustomerCheck], variant: int = 0
    ) -> ClaudeDashboardInsight:
        if not self.api_key:
            raise ClaudeServiceError("ANTHROPIC_API_KEY is not configured.")
        if not checks:
            raise ClaudeServiceError("Run or import at least one request before generating insight.")
        try:
            client = Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=700,
                system="You are a concise product strategy analyst.",
                messages=[{"role": "user", "content": self.build_dashboard_prompt(checks)}],
                extra_body={
                    "output_config": {
                        "format": {
                            "type": "json_schema",
                            "schema": ClaudeDashboardInsight.model_json_schema(),
                        }
                    }
                },
            )
            return ClaudeDashboardInsight.model_validate_json(response.content[0].text)
        except ClaudeServiceError:
            raise
        except Exception as exc:
            raise ClaudeServiceError(f"Claude insight request failed: {exc}") from exc
