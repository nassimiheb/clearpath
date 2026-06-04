from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ClearPath"
    database_url: str = "sqlite:///./data/clearpath.db"
    matching_provider: str = "claude"
    seed_demo_data: bool = False
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    notion_token: str = ""
    notion_data_source_id: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
