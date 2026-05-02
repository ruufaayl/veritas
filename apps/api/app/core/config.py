from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    NASA_FIRMS_API_KEY: str = ""
    SENTINEL_HUB_CLIENT_ID: str = ""
    SENTINEL_HUB_CLIENT_SECRET: str = ""
    NOAA_TOKEN: str = ""
    MAPBOX_TOKEN: str = ""
    GROQ_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GFW_API_KEY: str = ""
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/veritas"


settings = Settings()
