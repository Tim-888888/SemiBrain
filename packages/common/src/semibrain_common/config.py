from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEMIBRAIN_", extra="ignore")
    env: str = "development"
    demo_mode: bool = False
    # Foundation transport credential; service identity is not end-user authorization.
    internal_token: SecretStr = SecretStr("")
