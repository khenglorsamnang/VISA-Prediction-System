import math
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env", override=False)

BANK_STATEMENT_MAX_FILE_SIZE = 50 * 1024 * 1024


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())


def parse_jwt_lifetime(value: str) -> timedelta:
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|seconds?|m|minutes?|h|hours?|d|days?|w|weeks?)?\s*",
        value,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("JWT_EXPIRES_IN must be a valid duration")

    amount = float(match.group(1))
    unit = (match.group(2) or "ms").lower()
    multipliers = {
        "ms": 0.001,
        "millisecond": 0.001,
        "milliseconds": 0.001,
        "s": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "w": 604800,
        "week": 604800,
        "weeks": 604800,
    }
    seconds = amount * multipliers[unit]
    if seconds <= 0:
        raise ValueError("JWT_EXPIRES_IN must be greater than zero")
    return timedelta(seconds=seconds)


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    mongodb_uri: str
    mongodb_database: str
    jwt_secret: str
    jwt_expires_in: str
    cors_origins: tuple[str, ...]
    max_file_size: int
    log_level: str
    ai_service_url: str
    ai_service_timeout_seconds: float
    ocr_job_poll_interval_seconds: float = 0.5


def load_settings() -> Settings:
    mongodb_uri = os.getenv(
        "MONGODB_URI", "mongodb://localhost:27017/visadoc_ai"
    ).strip()
    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if not jwt_secret:
        if os.getenv("NODE_ENV", "").casefold() == "production":
            raise ValueError("JWT_SECRET must be set in production")
        jwt_secret = "dev_secret"
    uri_database = urlsplit(mongodb_uri).path.lstrip("/").split("/", 1)[0]
    max_file_size = int(os.getenv("MAX_FILE_SIZE", str(10 * 1024 * 1024)))
    if max_file_size < 1:
        raise ValueError("MAX_FILE_SIZE must be a positive number")
    ai_service_timeout_seconds = float(os.getenv("AI_SERVICE_TIMEOUT_SECONDS", "900"))
    if (
        not math.isfinite(ai_service_timeout_seconds)
        or ai_service_timeout_seconds <= 0
    ):
        raise ValueError("AI_SERVICE_TIMEOUT_SECONDS must be greater than zero")
    ocr_job_poll_interval_seconds = float(
        os.getenv("OCR_JOB_POLL_INTERVAL_SECONDS", "0.5")
    )
    if (
        not math.isfinite(ocr_job_poll_interval_seconds)
        or ocr_job_poll_interval_seconds <= 0
    ):
        raise ValueError("OCR_JOB_POLL_INTERVAL_SECONDS must be greater than zero")

    default_cors = (
        "http://localhost:5174,http://127.0.0.1:5174,"
        "http://localhost:5173,http://127.0.0.1:5173"
    )
    cors_origins = _csv_values(
        os.getenv(
            "CORS_ORIGINS",
            os.getenv("CLIENT_URL", default_cors),
        )
    )
    if not cors_origins:
        cors_origins = tuple(default_cors.split(","))

    jwt_expires_in = os.getenv("JWT_EXPIRES_IN", "7d").strip() or "7d"
    parse_jwt_lifetime(jwt_expires_in)

    return Settings(
        app_name=os.getenv("FASTAPI_APP_NAME", "VisaDoc AI API"),
        app_version=os.getenv("FASTAPI_APP_VERSION", "0.1.0"),
        mongodb_uri=mongodb_uri,
        mongodb_database=(
            os.getenv("MONGODB_DATABASE", "").strip()
            or uri_database
            or "visadoc_ai"
        ),
        jwt_secret=jwt_secret,
        jwt_expires_in=jwt_expires_in,
        cors_origins=cors_origins,
        max_file_size=max_file_size,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        ai_service_url=os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8002").rstrip("/"),
        ai_service_timeout_seconds=ai_service_timeout_seconds,
        ocr_job_poll_interval_seconds=ocr_job_poll_interval_seconds,
    )


settings = load_settings()
