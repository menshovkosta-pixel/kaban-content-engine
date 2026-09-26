from __future__ import annotations

from dataclasses import dataclass
import os

from kaban.runtime.paths import persistence_mode


@dataclass(frozen=True)
class CloudConfig:
    supabase_url: str
    supabase_service_key: str
    r2_endpoint: str
    r2_bucket: str
    r2_access_key_id: str
    r2_secret_access_key: str

    @staticmethod
    def _first_env(*names: str) -> str:
        for name in names:
            value = os.getenv(name, "").strip()
            if value:
                return value
        return ""

    @classmethod
    def from_env(cls) -> "CloudConfig | None":
        if persistence_mode() != "cloud":
            return None
        values = {
            "supabase_url": cls._first_env("KABAN_SUPABASE_URL"),
            "supabase_service_key": cls._first_env("KABAN_SUPABASE_SERVICE_KEY"),
            "r2_endpoint": cls._first_env("KABAN_R2_ENDPOINT_URL", "KABAN_R2_ENDPOINT"),
            "r2_bucket": cls._first_env("KABAN_R2_BUCKET"),
            "r2_access_key_id": cls._first_env("AWS_ACCESS_KEY_ID", "KABAN_R2_ACCESS_KEY_ID"),
            "r2_secret_access_key": cls._first_env("AWS_SECRET_ACCESS_KEY", "KABAN_R2_SECRET_ACCESS_KEY"),
        }
        labels = {
            "supabase_url": "KABAN_SUPABASE_URL",
            "supabase_service_key": "KABAN_SUPABASE_SERVICE_KEY",
            "r2_endpoint": "KABAN_R2_ENDPOINT_URL",
            "r2_bucket": "KABAN_R2_BUCKET",
            "r2_access_key_id": "AWS_ACCESS_KEY_ID",
            "r2_secret_access_key": "AWS_SECRET_ACCESS_KEY",
        }
        missing = [labels[field] for field, value in values.items() if not value]
        if missing:
            raise ValueError("Не заданы cloud credentials: " + ", ".join(missing))
        return cls(**values)
