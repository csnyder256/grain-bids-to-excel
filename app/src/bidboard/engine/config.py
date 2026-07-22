"""Engine configuration, sourced from the settings document's `advanced`
section plus fixed operational constants."""
from __future__ import annotations

from dataclasses import dataclass

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class EngineConfig:
    politeness_min_s: float = 2.0
    politeness_max_s: float = 5.0
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 20.0
    render_timeout_s: float = 30.0
    respect_robots: bool = True
    resolve_verify: bool = True
    max_retries: int = 2
    retry_backoff_s: tuple[float, float] = (5.0, 15.0)
    robots_cache_hours: int = 24

    @classmethod
    def from_settings(cls, settings: dict) -> "EngineConfig":
        adv = settings.get("advanced", {})
        return cls(
            politeness_min_s=adv.get("politeness_min_s", 2.0),
            politeness_max_s=adv.get("politeness_max_s", 5.0),
            read_timeout_s=float(adv.get("page_timeout_s", 30)),
            respect_robots=adv.get("respect_robots", True),
            resolve_verify=adv.get("resolve_verify", True),
        )
