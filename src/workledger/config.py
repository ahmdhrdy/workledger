import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKLEDGER_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///.local/workledger.db"
    mode: Literal["development", "production", "test"] = "development"
    cookie_secure: bool = False
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    session_hours: int = 12
    local_ai: bool = True
    local_data_dir: str = ".local/vision"
    model_dir: str = ".local/models"
    inference_threads: int = Field(
        default_factory=lambda: min(4, max(1, (os.cpu_count() or 2) // 2))
    )
    inference_fps: float = 2.0
    portal_inference_fps: float = 5.0
    face_threshold: float = 0.42
    face_margin: float = 0.08
    side_face_threshold: float = 0.42
    side_face_margin: float = 0.06
    attendance_face_threshold: float = 0.45
    attendance_face_margin: float = 0.08
    gallery_backend: Literal["local", "pgvector"] = "local"
    detector_size: int = Field(default=640, ge=320, le=1280, multiple_of=32)
    live_detection_threshold: float = Field(default=0.45, ge=0.3, le=1)
    enrollment_detection_threshold: float = Field(default=0.70, ge=0.5, le=1)
    enrollment_face_min_pixels: int = Field(default=65, ge=32, le=512)
    face_edge_margin: int = Field(default=0, ge=0, le=20)
    face_yaw_min: float = Field(default=0.20, ge=0, le=1)
    face_blur_min: float = Field(default=60.0, ge=0)
    face_verify_seconds: float = Field(default=5.0, gt=0, le=30)
    portal_verify_seconds: float = Field(default=0.5, gt=0, le=2)
    masked_augmentation: bool = False
    reid_threshold: float = 0.82
    reid_margin: float = 0.08
    face_min_pixels: int = Field(default=32, ge=32, le=256)
    confirmation_frames: int = 3
    track_lost_seconds: float = Field(default=5.0, ge=1, le=10)
    recovery_seconds: float = Field(default=8.0, ge=5, le=15)
    identity_vote_seconds: float = Field(default=8.0, ge=2, le=30)
    identity_switch_score: float = Field(default=0.65, ge=0.5, le=1)
    identity_switch_margin: float = Field(default=0.15, ge=0.08, le=1)
    max_active_cameras: int = 12
    visual_tracking_fps: int = Field(default=10, ge=2, le=15)
    live_fps: int = 15
    decode_threads: int = Field(default=2, ge=1, le=4)
    unknown_face_retry_seconds: float = Field(default=1.0, gt=0, le=5)
    body_feature_seconds: float = Field(default=2.0, gt=0, le=10)
    person_detection_threshold: float = Field(default=0.3, ge=0.1, le=0.95)

    def validate_production(self):
        if self.mode == "production":
            if not self.database_url.startswith("postgresql") or not self.cookie_secure:
                raise RuntimeError("Production requires PostgreSQL and secure HTTPS cookies")
            if not self.allowed_origins or any(
                not x.startswith("https://") for x in self.allowed_origins
            ):
                raise RuntimeError("Production requires explicit HTTPS origins")


def ensure_sqlite_parent(url: str):
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
