import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TargetConfig:
    country: Optional[str] = None
    city: Optional[str] = None
    country_id: Optional[str] = None
    city_id: Optional[str] = None


@dataclass
class FilterConfig:
    private_bathroom: Optional[bool] = None
    private_kitchen: Optional[bool] = None
    max_weekly_price: Optional[float] = None
    max_monthly_price: Optional[float] = None


@dataclass
class Semester1Rules:
    name_keywords: List[str] = field(default_factory=lambda: ["semester 1"])
    require_keyword: bool = True
    start_months: List[int] = field(default_factory=lambda: [9, 10])
    end_months: List[int] = field(default_factory=lambda: [1, 2])
    enforce_month_window: bool = True


@dataclass
class AcademicYearConfig:
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    semester1: Semester1Rules = field(default_factory=Semester1Rules)

    def academic_year_str(self) -> str:
        """Return e.g. '2026-27' from start/end years."""
        if self.start_year and self.end_year:
            short_end = self.end_year % 100
            return f"{self.start_year}-{short_end:02d}"
        now = datetime.now()
        if now.month >= 8:
            start_year = now.year
            end_year = now.year + 1
        else:
            start_year = now.year - 1
            end_year = now.year
        return f"{start_year}-{end_year % 100:02d}"


@dataclass
class ProviderConfig:
    enabled: bool = True


@dataclass
class ProvidersConfig:
    yugo_enabled: bool = True
    aparto_enabled: bool = True
    aparto_term_id_start: int = 1200
    aparto_term_id_end: int = 1600


@dataclass
class PollingConfig:
    interval_seconds: int = 300
    jitter_seconds: int = 30


# ---------------------------------------------------------------------------
# Notifier configs
# ---------------------------------------------------------------------------

@dataclass
class StdoutNotifierConfig:
    enabled: bool = True


@dataclass
class WebhookNotifierConfig:
    enabled: bool = False
    url: Optional[str] = None
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None  # Use {message} placeholder


@dataclass
class TelegramNotifierConfig:
    enabled: bool = False
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    parse_mode: Optional[str] = None  # "HTML" | "Markdown" | None


@dataclass
class OpenClawNotifierConfig:
    enabled: bool = False
    mode: str = "message"  # message | agent
    channel: str = "telegram"
    target: Optional[str] = None
    create_job_on_match: bool = False
    reservation_mode: str = "assist"  # assist | autobook
    job_model: str = "anthropic/claude-sonnet-4-6"
    job_timeout_seconds: int = 600
    job_channel: Optional[str] = None
    job_target: Optional[str] = None


@dataclass
class NotificationConfig:
    # Which notifier to use: stdout | webhook | telegram | openclaw
    type: str = "stdout"
    stdout: StdoutNotifierConfig = field(default_factory=StdoutNotifierConfig)
    webhook: WebhookNotifierConfig = field(default_factory=WebhookNotifierConfig)
    telegram: TelegramNotifierConfig = field(default_factory=TelegramNotifierConfig)
    openclaw: OpenClawNotifierConfig = field(default_factory=OpenClawNotifierConfig)


@dataclass
class Config:
    target: TargetConfig = field(default_factory=TargetConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    academic_year: AcademicYearConfig = field(default_factory=AcademicYearConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _get_dict(data: dict, key: str, default: Optional[dict] = None) -> dict:
    value = data.get(key)
    if value is None:
        return default or {}
    if isinstance(value, dict):
        return value
    return default or {}


def _as_int_list(value: Any, fallback: List[int]) -> List[int]:
    if not isinstance(value, list):
        return fallback
    out = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out or fallback


def _load_yaml(path: str) -> Tuple[dict, List[str]]:
    warnings: List[str] = []
    if not os.path.exists(path):
        warnings.append(f"Config file not found at {path}; using defaults.")
        return {}, warnings
    try:
        import yaml  # type: ignore
    except ImportError:
        warnings.append("PyYAML is not installed; skipping YAML config load.")
        return {}, warnings

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        warnings.append(f"YAML parse error in {path}: {exc}")
        return {}, warnings

    if not isinstance(data, dict):
        warnings.append("Config root must be a mapping; using defaults.")
        return {}, warnings
    return data, warnings


def load_config(yaml_path: str = "config.yaml") -> Tuple[Config, List[str]]:
    data, warnings = _load_yaml(yaml_path)

    target_data = _get_dict(data, "target", {})
    filter_data = _get_dict(data, "filters", {})
    academic_data = _get_dict(data, "academic_year", {})
    semester_data = _get_dict(academic_data, "semester1", {})
    polling_data = _get_dict(data, "polling", {})
    notify_data = _get_dict(data, "notifications", {})
    providers_data = _get_dict(data, "providers", {})

    # Parse notifier configs
    notify_type = str(notify_data.get("type", "stdout"))

    # Webhook config
    webhook_data = _get_dict(notify_data, "webhook", {})
    webhook_headers = webhook_data.get("headers", {})
    if not isinstance(webhook_headers, dict):
        webhook_headers = {}

    # Telegram config
    telegram_data = _get_dict(notify_data, "telegram", {})

    # OpenClaw config
    openclaw_data = _get_dict(notify_data, "openclaw", {})

    config = Config(
        target=TargetConfig(
            country=target_data.get("country"),
            city=target_data.get("city"),
            country_id=str(target_data["country_id"]) if target_data.get("country_id") is not None else None,
            city_id=str(target_data["city_id"]) if target_data.get("city_id") is not None else None,
        ),
        filters=FilterConfig(
            private_bathroom=filter_data.get("private_bathroom"),
            private_kitchen=filter_data.get("private_kitchen"),
            max_weekly_price=filter_data.get("max_weekly_price"),
            max_monthly_price=filter_data.get("max_monthly_price"),
        ),
        academic_year=AcademicYearConfig(
            start_year=academic_data.get("start_year"),
            end_year=academic_data.get("end_year"),
            semester1=Semester1Rules(
                name_keywords=semester_data.get("name_keywords")
                if isinstance(semester_data.get("name_keywords"), list)
                else Semester1Rules().name_keywords,
                require_keyword=bool(semester_data.get("require_keyword", True)),
                start_months=_as_int_list(semester_data.get("start_months"), [9, 10]),
                end_months=_as_int_list(semester_data.get("end_months"), [1, 2]),
                enforce_month_window=bool(semester_data.get("enforce_month_window", True)),
            ),
        ),
        polling=PollingConfig(
            interval_seconds=int(polling_data.get("interval_seconds", 300)),
            jitter_seconds=int(polling_data.get("jitter_seconds", 30)),
        ),
        notifications=NotificationConfig(
            type=notify_type,
            stdout=StdoutNotifierConfig(enabled=True),
            webhook=WebhookNotifierConfig(
                enabled=bool(webhook_data.get("enabled", False)),
                url=webhook_data.get("url"),
                method=str(webhook_data.get("method", "POST")),
                headers=webhook_headers,
                body_template=webhook_data.get("body_template"),
            ),
            telegram=TelegramNotifierConfig(
                enabled=bool(telegram_data.get("enabled", False)),
                bot_token=telegram_data.get("bot_token"),
                chat_id=str(telegram_data["chat_id"]) if telegram_data.get("chat_id") is not None else None,
                parse_mode=telegram_data.get("parse_mode"),
            ),
            openclaw=OpenClawNotifierConfig(
                enabled=bool(openclaw_data.get("enabled", False)),
                mode=str(openclaw_data.get("mode", "message")),
                channel=str(openclaw_data.get("channel", "telegram")),
                target=str(openclaw_data["target"]) if openclaw_data.get("target") is not None else None,
                create_job_on_match=bool(openclaw_data.get("create_job_on_match", False)),
                reservation_mode=str(openclaw_data.get("reservation_mode", "assist")),
                job_model=str(openclaw_data.get("job_model", "anthropic/claude-sonnet-4-6")),
                job_timeout_seconds=int(openclaw_data.get("job_timeout_seconds", 600)),
                job_channel=openclaw_data.get("job_channel"),
                job_target=str(openclaw_data["job_target"]) if openclaw_data.get("job_target") is not None else None,
            ),
        ),
        providers=ProvidersConfig(
            yugo_enabled=bool(_get_dict(providers_data, "yugo", {}).get("enabled", True)),
            aparto_enabled=bool(_get_dict(providers_data, "aparto", {}).get("enabled", True)),
            aparto_term_id_start=int(_get_dict(providers_data, "aparto", {}).get("term_id_start", 1200)),
            aparto_term_id_end=int(_get_dict(providers_data, "aparto", {}).get("term_id_end", 1600)),
        ),
    )

    return config, warnings
