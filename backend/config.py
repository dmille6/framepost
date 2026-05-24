from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str = ""
    token_encryption_key: str = ""

    database_url: str = "sqlite:////app/data/framepost.db"
    photo_root: str = "/mnt/photo-data"

    flickr_api_key: str = ""
    flickr_api_secret: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Pinterest app keys — register at developers.pinterest.com (free Trial mode is fine
    # for posting to your own account; production review only needed if other users will
    # connect via this app).
    pinterest_app_id: str = ""
    pinterest_app_secret: str = ""

    upload_max_mb: int = 200
    session_timeout_minutes: int = 1440


settings = Settings()
