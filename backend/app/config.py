"""Service configuration, read from environment variables and an optional .env file."""

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, get_args
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.addresses import canonical_address
from app.input_check import NotMriThresholds

_NOT_MRI_DEFAULTS = NotMriThresholds()
# Largest possible standard deviation of brightness values from 0 to 255.
MAX_STD = 127.5

ClassName = Literal["glioma", "meningioma", "notumor", "pituitary"]

# Order of the network outputs. It is fixed by the trained weights
# (alphabetical order of the dataset folders), so it is not configurable.
CLASS_NAMES: tuple[ClassName, ...] = get_args(ClassName)

# Other sizes get the unexpected_image_size warning: in cross-validation of
# resnet18_v2c, glioma recall was about 0.51 on them against about 0.99 on 512x512,
# so a suspected glioma on such a scan is less reliable.
EXPECTED_IMAGE_SIZE = (512, 512)

# Codes of the warnings /predict returns. /stats reports each of them, even at zero.
UNEXPECTED_IMAGE_SIZE = "unexpected_image_size"
LOW_CONFIDENCE = "low_confidence"
# The image does not look like an MRI scan at all (see app.input_check).
NOT_MRI_LIKE = "not_mri_like"
WARNING_CODES = (UNEXPECTED_IMAGE_SIZE, LOW_CONFIDENCE, NOT_MRI_LIKE)

# PadToSquare turns a scan into a side x side canvas, so a long thin image that is
# within MAX_PIXELS (16384x1024) would still need a 16384 x 16384 canvas.
DEFAULT_MAX_IMAGE_SIDE = 4096

# Host headers the API answers to: this machine, and "api", the service name in Compose.
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "api")
# Always allowed, whatever ALLOWED_HOSTS says: the container healthcheck asks 127.0.0.1.
# This does not weaken the DNS rebinding check: a rebinding attack arrives with the
# attacker's host name in the Host header, never with these.
LOCAL_HOSTS = ("localhost", "127.0.0.1")
# Pages that may send POST and DELETE: the Vite dev server (5173), `npm run preview`
# (4173) and Swagger UI served by the API itself (8000).
DEFAULT_ALLOWED_ORIGINS = tuple(
    f"http://{host}:{port}" for port in (5173, 4173, 8000) for host in ("localhost", "127.0.0.1")
)


def comma_separated(value: object) -> object:
    """'a, b' -> ['a', 'b'], so lists are written in .env without JSON brackets."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """Settings taken from MODEL_PATH, MODEL_VERSION, DEVICE, MAX_UPLOAD_MB, MAX_PIXELS,
    MAX_IMAGE_SIDE, MAX_CONCURRENT_INFERENCES, MAX_QUEUE, LOW_CONFIDENCE_THRESHOLD,
    NOT_MRI_COLOR_MAX, NOT_MRI_NOISE_MAX, NOT_MRI_STD_MIN, NOT_MRI_LOWCONTRAST_STD,
    NOT_MRI_DARK_MIN, NOT_MRI_OPAQUE_MIN, DATABASE_URL, ALLOWED_HOSTS, ALLOWED_ORIGINS,
    ENABLE_DOCS, RATE_LIMIT_ENABLED, RATE_LIMIT_PREDICT_PER_MINUTE, RATE_LIMIT_WRITE_PER_MINUTE,
    RATE_LIMIT_READ_PER_MINUTE and TRUSTED_PROXIES."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    model_path: Path = Path("models/resnet18_v2c_best.pt")
    # Name of the weights in MODEL_PATH. It is returned by /health and /predict and
    # saved with every prediction, so change it together with MODEL_PATH.
    model_version: str = Field(default="resnet18_v2c", min_length=1, max_length=64)
    device: str = "cpu"
    max_upload_mb: float = Field(default=10, gt=0)
    max_pixels: int = Field(default=16_777_216, gt=0)  # 4096 x 4096
    # Longest allowed side of a scan, in pixels.
    max_image_side: int = Field(default=DEFAULT_MAX_IMAGE_SIDE, gt=0)
    # Predictions that run at the same time; up to MAX_QUEUE more wait for a free slot,
    # and /predict answers 503 to the rest.
    max_concurrent_inferences: int = Field(default=2, gt=0)
    max_queue: int = Field(default=20, ge=0)
    # A prediction whose top-1 probability is below this gets a low_confidence warning.
    low_confidence_threshold: float = Field(default=0.9, ge=0, le=1)
    # A prediction gets a not_mri_like warning when at least one holds (all strict):
    # colour > NOT_MRI_COLOR_MAX, noise > NOT_MRI_NOISE_MAX, brightness std < NOT_MRI_STD_MIN,
    # or std < NOT_MRI_LOWCONTRAST_STD together with a dark share < NOT_MRI_DARK_MIN.
    not_mri_color_max: float = Field(default=_NOT_MRI_DEFAULTS.color_max, ge=0, le=255)
    not_mri_noise_max: float = Field(default=_NOT_MRI_DEFAULTS.noise_max, ge=0)
    # The std of brightness values from 0 to 255 is at most 127.5, so a higher threshold
    # would flag every scan.
    not_mri_std_min: float = Field(default=_NOT_MRI_DEFAULTS.std_min, ge=0, le=MAX_STD)
    not_mri_lowcontrast_std: float = Field(
        default=_NOT_MRI_DEFAULTS.lowcontrast_std, ge=0, le=MAX_STD
    )
    not_mri_dark_min: float = Field(default=_NOT_MRI_DEFAULTS.dark_min, ge=0, le=1)
    # ... or when the share of fully opaque pixels is below NOT_MRI_OPAQUE_MIN.
    not_mri_opaque_min: float = Field(default=_NOT_MRI_DEFAULTS.opaque_min, ge=0, le=1)
    # SQLAlchemy URL of the prediction history, e.g.
    # postgresql+psycopg://user:password@localhost:5432/brain_mri.
    # Without it the service still predicts, but keeps no history.
    database_url: str | None = None
    # Host headers to answer; others get 400. "*" allows any host. LOCAL_HOSTS are added.
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default=list(DEFAULT_ALLOWED_HOSTS), min_length=1
    )
    # Origins (scheme://host[:port]) whose pages may send POST and DELETE; a request
    # with any other Origin header gets 403. Requests without Origin are not browsers'.
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=list(DEFAULT_ALLOWED_ORIGINS)
    )
    # Serve Swagger UI (/docs), ReDoc (/redoc) and the schema (/openapi.json).
    enable_docs: bool = True
    # Requests per client in any 60 s: POST /predict, other writes (DELETE), reads (GET).
    # Above the limit the API answers 429 with Retry-After; /health is never limited.
    rate_limit_enabled: bool = True
    rate_limit_predict_per_minute: int = Field(default=10, gt=0)
    rate_limit_write_per_minute: int = Field(default=30, gt=0)
    rate_limit_read_per_minute: int = Field(default=120, gt=0)
    # Addresses of reverse proxies whose X-Real-IP header names the client (comma-separated).
    # Empty: the header is ignored and the client is the peer of the connection.
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("allowed_hosts", "allowed_origins", "trusted_proxies", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        return comma_separated(value)

    @field_validator("allowed_hosts")
    @classmethod
    def _normalise_hosts(cls, hosts: list[str]) -> list[str]:
        hosts = [host.lower() for host in hosts]
        if "*" in hosts:
            return hosts
        return hosts + [host for host in LOCAL_HOSTS if host not in hosts]

    @field_validator("trusted_proxies")
    @classmethod
    def _check_proxies(cls, proxies: list[str]) -> list[str]:
        """Single addresses only: trusting a network would also trust its gateway, the
        address direct connections to a published port come from."""
        checked = []
        for proxy in proxies:
            try:
                checked.append(str(canonical_address(ipaddress.ip_address(proxy))))
            except ValueError as exc:
                raise ValueError(f"{proxy!r} is not an IP address") from exc
        return checked

    @field_validator("allowed_origins")
    @classmethod
    def _check_origins(cls, origins: list[str]) -> list[str]:
        checked = []
        for origin in origins:
            parts = urlsplit(origin)
            if (
                parts.scheme not in ("http", "https")
                or not parts.netloc
                or parts.path not in ("", "/")
                or parts.query
                or parts.fragment
            ):
                raise ValueError(
                    f"{origin!r} is not an origin: use scheme://host[:port], e.g. "
                    "https://mri.example"
                )
            checked.append(f"{parts.scheme}://{parts.netloc}".lower())
        return checked

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def trusted_proxy_addresses(self) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        return frozenset(ipaddress.ip_address(proxy) for proxy in self.trusted_proxies)

    @property
    def not_mri_thresholds(self) -> NotMriThresholds:
        return NotMriThresholds(
            color_max=self.not_mri_color_max,
            noise_max=self.not_mri_noise_max,
            std_min=self.not_mri_std_min,
            lowcontrast_std=self.not_mri_lowcontrast_std,
            dark_min=self.not_mri_dark_min,
            opaque_min=self.not_mri_opaque_min,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
