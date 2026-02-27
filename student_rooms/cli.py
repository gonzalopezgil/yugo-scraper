"""
student_rooms.cli — student-rooms-cli entry point.

Commands: discover | scan | watch | probe-booking | notify | test-match
Providers: --provider yugo | aparto | all  (default: all)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from student_rooms.matching import apply_filters
from student_rooms.models.config import Config, load_config
from student_rooms.notifiers.base import create_notifier
from student_rooms.providers.base import RoomOption

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dedup persistence
# ---------------------------------------------------------------------------

LEGACY_SEEN_FILE = os.path.join(os.path.dirname(__file__), "..", "reports", "seen_options.json")


def _default_seen_path() -> str:
    """Return path for seen_options.json, preferring user data dir with legacy fallback."""
    legacy_path = os.path.abspath(LEGACY_SEEN_FILE)
    if os.path.exists(legacy_path):
        return legacy_path

    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join("~", ".local", "share")
    data_dir = os.path.expanduser(os.path.join(data_home, "student-rooms-cli"))
    return os.path.join(data_dir, "seen_options.json")


def load_seen_keys(path: Optional[str] = None) -> Set[str]:
    path = path or _default_seen_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, list):
                return set(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return set()


def save_seen_keys(keys: Set[str], path: Optional[str] = None) -> None:
    path = path or _default_seen_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sorted(keys), fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def make_providers(
    provider_arg: str,
    config: Config,
    country: Optional[str] = None,
    city: Optional[str] = None,
    country_id: Optional[str] = None,
    city_id: Optional[str] = None,
) -> List[Any]:
    """Return list of provider instances based on --provider flag."""
    providers_cfg = getattr(config, "providers", None)
    yugo_enabled = True
    aparto_enabled = True
    if providers_cfg:
        yugo_enabled = getattr(providers_cfg, "yugo_enabled", True)
        aparto_enabled = getattr(providers_cfg, "aparto_enabled", True)

    instances = []

    want_yugo = provider_arg in ("yugo", "all")
    want_aparto = provider_arg in ("aparto", "all")

    if want_yugo and yugo_enabled:
        from student_rooms.providers.yugo import YugoProvider
        instances.append(YugoProvider(
            country=country or config.target.country or "Ireland",
            city=city or config.target.city or "Dublin",
            country_id=country_id or config.target.country_id,
            city_id=city_id or config.target.city_id,
        ))

    if want_aparto and aparto_enabled:
        from student_rooms.providers.aparto import ApartoProvider
        aparto_start = getattr(providers_cfg, "aparto_term_id_start", None) if providers_cfg else None
        aparto_end = getattr(providers_cfg, "aparto_term_id_end", None) if providers_cfg else None
        instances.append(ApartoProvider(
            city=city or config.target.city or "Dublin",
            country=country or config.target.country,
            term_id_start=aparto_start or 1200,
            term_id_end=aparto_end or 1600,
        ))

    return instances


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(getattr(h, "_student_rooms_handler", False) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(fmt)
    handler._student_rooms_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Alert messages
# ---------------------------------------------------------------------------

def build_alert_message(
    matches: List[RoomOption],
    provider_probe: Optional[Dict[str, Any]] = None,
    is_new: bool = True,
    all_options: bool = False,
) -> str:
    """Build alert message for matched room options (multi-provider)."""
    if not matches:
        return ""

    top = matches[0]
    flag = "🚨 NEW" if is_new else "🔁 REMINDER"
    header = "Availability detected" if all_options else "Semester 1 detected"

    lines = [
        f"{flag} · Student Rooms · {header}",
        "",
        "⭐ Top match:",
    ]
    lines.extend(top.alert_lines())

    # Add probe link if available
    if provider_probe:
        link = (
            provider_probe.get("links", {}).get("skipRoomLink")
            or provider_probe.get("links", {}).get("handoverLink")
            or provider_probe.get("links", {}).get("bookingPortal")
        )
        if link:
            lines.append(f"🔗 Book: {link}")

    if len(matches) > 1:
        lines.extend(["", f"📋 {len(matches)} total options (top 5 alternatives):"])
        for idx, m in enumerate(matches[1:6], start=2):
            price = f"€{m.price_weekly:.0f}/week" if m.price_weekly else m.price_label or "N/A"
            lines.append(f"  {idx}. [{m.provider.upper()}] {m.property_name} | {m.room_type} | {price}")

    return "\n".join(lines)


def prioritize_matches(matches: List[RoomOption]) -> List[RoomOption]:
    """Sort: available first, then by provider preference, then by price."""
    def key(m: RoomOption) -> Tuple:
        available_rank = 0 if m.available else 1
        price_rank = m.price_weekly if m.price_weekly is not None else float("inf")
        return (available_rank, m.provider, m.property_name, m.room_type, price_rank)
    return sorted(matches, key=key)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def handle_discover(args: argparse.Namespace, config: Config) -> int:
    providers = make_providers(
        args.provider, config,
        country=getattr(args, "country", None),
        city=getattr(args, "city", None),
        country_id=getattr(args, "country_id", None),
        city_id=getattr(args, "city_id", None),
    )

    if getattr(args, "countries", False) or getattr(args, "cities", False) or getattr(args, "residences", False):
        yugo_provider = next((p for p in providers if p.name == "yugo"), None)
        if not yugo_provider:
            print("Yugo provider not enabled; listing flags are only supported for Yugo.")
            return 2

        if args.countries:
            items = yugo_provider.list_countries()
            label = "countries"
        elif args.cities:
            items = yugo_provider.list_cities()
            label = "cities"
        else:
            items = yugo_provider.list_residences()
            label = "residences"

        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            print(f"Found {len(items)} {label}:")
            for item in items:
                name = item.get("name") or item.get("displayName") or item.get("contentId") or item.get("id") or str(item)
                item_id = item.get("contentId") or item.get("id") or item.get("countryId") or ""
                suffix = f" ({item_id})" if item_id else ""
                print(f"- {name}{suffix}")
        return 0

    all_props: List[Dict] = []
    for p in providers:
        props = p.discover_properties()
        for prop in props:
            prop.setdefault("provider", p.name)
        all_props.extend(props)

    if args.json:
        print(json.dumps(all_props, ensure_ascii=False, indent=2))
    else:
        print(f"Found {len(all_props)} properties:\n")
        for prop in all_props:
            prov = prop.get("provider", "?")
            name = prop.get("name") or prop.get("contentId") or prop.get("id") or str(prop)
            slug = prop.get("slug") or prop.get("id") or ""
            loc = prop.get("locationInfo") or prop.get("location") or ""
            url = prop.get("url") or prop.get("portalLink") or ""
            print(f"[{prov.upper()}] {name} ({slug})")
            if loc:
                print(f"       📍 {loc}")
            if url:
                print(f"       🔗 {url}")
    return 0


def handle_scan(args: argparse.Namespace, config: Config) -> int:
    providers = make_providers(
        args.provider, config,
        country=getattr(args, "country", None),
        city=getattr(args, "city", None),
        country_id=getattr(args, "country_id", None),
        city_id=getattr(args, "city_id", None),
    )

    academic_year = config.academic_year.academic_year_str()
    apply_filter = not getattr(args, "all_options", False)

    all_matches: List[RoomOption] = []
    for p in providers:
        try:
            matches = p.scan(
                academic_year=academic_year,
                semester=1,
                apply_semester_filter=apply_filter,
                academic_config=config.academic_year,
            )
            all_matches.extend(matches)
        except Exception:
            logger.exception("Provider %s scan failed", p.name)

    filtered = apply_filters(all_matches, config.filters)
    ranked = prioritize_matches(filtered)

    if args.json:
        print(json.dumps(
            {
                "matchCount": len(ranked),
                "matches": [
                    {
                        "provider": m.provider,
                        "property": m.property_name,
                        "roomType": m.room_type,
                        "priceWeekly": m.price_weekly,
                        "priceLabel": m.price_label,
                        "available": m.available,
                        "bookingUrl": m.booking_url,
                        "startDate": m.start_date,
                        "endDate": m.end_date,
                        "optionName": m.option_name,
                        "location": m.location,
                        "dedupKey": m.dedup_key(),
                    }
                    for m in ranked
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        if ranked:
            for m in ranked[:10]:
                price = f"€{m.price_weekly:.0f}/week" if m.price_weekly else m.price_label or "N/A"
                print(f"[{m.provider.upper()}] {m.property_name} | {m.room_type} | {price}")
                if m.option_name:
                    print(f"         Tenancy: {m.option_name}")
                if m.location:
                    print(f"         📍 {m.location}")
                if m.booking_url:
                    print(f"         🔗 {m.booking_url}")
                print()
        print(f"Total matches: {len(ranked)}")

    if getattr(args, "notify", False) and ranked:
        notifier = create_notifier(config.notifications)
        error = notifier.validate()
        if error:
            print(error)
            return 2

        probe = None
        top = ranked[0]
        try:
            provider_inst = next(
                p for p in providers if p.name == top.provider
            )
            probe = provider_inst.probe_booking(top)
        except (StopIteration, NotImplementedError, Exception) as exc:
            logger.warning("Booking probe for notify failed: %s", exc)

        message = build_alert_message(ranked, probe, is_new=True, all_options=not apply_filter)
        notifier.send(message)

    return 0


def handle_watch(args: argparse.Namespace, config: Config) -> int:
    providers = make_providers(
        args.provider, config,
        country=getattr(args, "country", None),
        city=getattr(args, "city", None),
        country_id=getattr(args, "country_id", None),
        city_id=getattr(args, "city_id", None),
    )

    academic_year = config.academic_year.academic_year_str()
    interval = max(5, config.polling.interval_seconds)
    jitter = max(0, config.polling.jitter_seconds)

    notifier = create_notifier(config.notifications)

    seen_keys = load_seen_keys()
    failure_counts: Dict[str, int] = {p.name: 0 for p in providers}
    backoff_until: Dict[str, float] = {p.name: 0.0 for p in providers}
    backoff_base = 30
    backoff_max = 600
    logger.info(
        "Watch loop started: providers=%s interval=%ds jitter=%ds seen=%d keys",
        [p.name for p in providers],
        interval,
        jitter,
        len(seen_keys),
    )
    print(
        f"▶ Watch started | providers: {', '.join(p.name for p in providers)} | "
        f"interval: {interval}s | academic year: {academic_year}"
    )

    try:
        while True:
            all_matches: List[RoomOption] = []
            for p in providers:
                now = time.monotonic()
                if now < backoff_until.get(p.name, 0.0):
                    logger.info(
                        "Skipping %s due to backoff (%.0fs remaining)",
                        p.name,
                        backoff_until[p.name] - now,
                    )
                    continue
                try:
                    matches = p.scan(
                        academic_year=academic_year,
                        semester=1,
                        apply_semester_filter=True,
                        academic_config=config.academic_year,
                    )
                    all_matches.extend(matches)
                    failure_counts[p.name] = 0
                    backoff_until[p.name] = 0.0
                except Exception:
                    logger.exception("Provider %s watch scan failed", p.name)
                    failure_counts[p.name] = failure_counts.get(p.name, 0) + 1
                    backoff = min(backoff_max, backoff_base * (2 ** (failure_counts[p.name] - 1)))
                    backoff_until[p.name] = time.monotonic() + backoff
                    logger.warning("Provider %s backoff set to %ds", p.name, backoff)

            filtered = apply_filters(all_matches, config.filters)
            ranked = prioritize_matches(filtered)
            logger.info("Scanned %s options. Total matches: %s", len(all_matches), len(ranked))

            # Detect new options not yet seen
            new_matches = [m for m in ranked if m.dedup_key() not in seen_keys]

            if new_matches:
                logger.info("NEW options detected: %d", len(new_matches))
                print(f"✅ NEW match(es) detected: {len(new_matches)}")

                error = notifier.validate()
                if error:
                    logger.error(error)
                else:
                    # Try to get booking probe for top new match
                    probe = None
                    top_new = new_matches[0]
                    try:
                        provider_inst = next(
                            p for p in providers if p.name == top_new.provider
                        )
                        probe = provider_inst.probe_booking(top_new)
                    except (StopIteration, NotImplementedError, Exception) as exc:
                        logger.warning("Watch probe failed: %s", exc)

                    message = build_alert_message(new_matches, probe, is_new=True, all_options=False)
                    notifier.send(message)

                # Add new keys to seen set and persist
                for m in new_matches:
                    seen_keys.add(m.dedup_key())
                save_seen_keys(seen_keys)

            else:
                print(f"  ⏳ {len(ranked)} matches, no new options. Sleeping {interval}s...")
                logger.info("No new matches. All %d matches already seen.", len(ranked))

            sleep_for = interval + random.randint(0, jitter) if jitter else interval
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        print("\n⏹ Watch stopped.")
        return 0


def handle_probe_booking(args: argparse.Namespace, config: Config) -> int:
    providers = make_providers(
        args.provider, config,
        country=getattr(args, "country", None),
        city=getattr(args, "city", None),
        country_id=getattr(args, "country_id", None),
        city_id=getattr(args, "city_id", None),
    )

    academic_year = config.academic_year.academic_year_str()
    apply_filter = not getattr(args, "all_options", False)

    all_matches: List[RoomOption] = []
    for p in providers:
        try:
            matches = p.scan(
                academic_year=academic_year,
                semester=1,
                apply_semester_filter=apply_filter,
                academic_config=config.academic_year,
            )
            all_matches.extend(matches)
        except Exception as exc:
            logger.error("Provider %s probe scan failed: %s", p.name, exc)

    if not all_matches:
        print("No matches found.")
        return 1

    # Apply optional filters
    def _contains(value: Optional[str], needle: Optional[str]) -> bool:
        if not needle:
            return True
        if not value:
            return False
        return needle.strip().lower() in value.strip().lower()

    candidates = [
        m for m in all_matches
        if _contains(m.property_name, getattr(args, "residence", None))
        and _contains(m.room_type, getattr(args, "room", None))
        and _contains(m.option_name, getattr(args, "tenancy", None))
        and (not getattr(args, "provider_filter", None) or m.provider == args.provider_filter)
    ]
    candidates = prioritize_matches(candidates)

    if not candidates:
        print("No candidates after filters.")
        return 1

    idx = max(0, getattr(args, "index", 0))
    if idx >= len(candidates):
        print(f"Index {idx} out of range (candidates: {len(candidates)})")
        return 2

    selected = candidates[idx]

    try:
        provider_inst = next(p for p in providers if p.name == selected.provider)
        probe = provider_inst.probe_booking(selected)
    except (StopIteration, NotImplementedError) as exc:
        print(f"Provider '{selected.provider}' does not support probe_booking: {exc}")
        return 1
    except Exception as exc:
        print(f"Booking probe failed: {exc}")
        return 1

    if getattr(args, "notify", False):
        notifier = create_notifier(config.notifications)
        error = notifier.validate()
        if error:
            print(error)
            return 2
        message = build_alert_message(candidates, probe, is_new=True, all_options=not apply_filter)
        notifier.send(message)

    if args.json:
        print(json.dumps(probe, ensure_ascii=False, indent=2))
    else:
        print("Booking probe OK")
        match = probe.get("match", {})
        links = probe.get("links", {})
        print(f"  Provider:  {selected.provider}")
        print(f"  Property:  {match.get('property') or match.get('residence')}")
        print(f"  Room:      {match.get('room')}")
        if match.get("startDate") or match.get("endDate"):
            print(f"  Dates:     {match.get('startDate')} → {match.get('endDate')}")
        for link_name, link_url in links.items():
            if link_url:
                print(f"  {link_name}: {link_url}")

    return 0


def handle_notify(args: argparse.Namespace, config: Config) -> int:
    notifier = create_notifier(config.notifications)
    error = notifier.validate()
    if error:
        print(error)
        return 2
    message = args.message or "Student Rooms notification test 🏠"
    notifier.send(message)
    print("Notification dispatched.")
    return 0


def handle_test_match(args: argparse.Namespace, config: Config) -> int:
    """Backwards-compatible test-match for Yugo semester logic."""
    from student_rooms.matching import match_semester1
    option = {
        "fromYear": args.from_year,
        "toYear": args.to_year,
        "tenancyOption": [{
            "name": args.name,
            "formattedLabel": args.label,
            "startDate": args.start_date,
            "endDate": args.end_date,
        }],
    }
    matched = match_semester1(option, config.academic_year)
    if args.json:
        print(json.dumps({"match": bool(matched)}, ensure_ascii=False))
    else:
        print("MATCH" if matched else "NO MATCH")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _add_provider_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=["yugo", "aparto", "all"],
        default="all",
        help="Provider to use: yugo | aparto | all (default: all)",
    )


def _add_location_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--country", help="Country name.")
    parser.add_argument("--country-id", dest="country_id", help="Country ID (Yugo only).")
    parser.add_argument("--city", help="City name.")
    parser.add_argument("--city-id", dest="city_id", help="City ID (Yugo only).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="student-rooms",
        description="student-rooms-cli — Multi-provider student accommodation finder and monitor",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config.")

    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    discover = sub.add_parser("discover", help="List available properties from providers.")
    _add_provider_arg(discover)
    _add_location_args(discover)
    discover.add_argument("--json", action="store_true", help="Output JSON.")
    discover.add_argument("--countries", action="store_true", help="[Yugo] List countries.")
    discover.add_argument("--cities", action="store_true", help="[Yugo] List cities.")
    discover.add_argument("--residences", action="store_true", help="[Yugo] List residences.")

    # scan
    scan = sub.add_parser("scan", help="Single-pass scan for semester availability.")
    _add_provider_arg(scan)
    _add_location_args(scan)
    scan.add_argument("--all-options", action="store_true", dest="all_options",
                      help="Skip semester filter, return all options.")
    scan.add_argument("--notify", action="store_true", help="Send notification for top match.")
    scan.add_argument("--json", action="store_true", help="Output JSON.")

    # watch
    watch = sub.add_parser("watch", help="Continuous monitoring loop with alerts.")
    _add_provider_arg(watch)
    _add_location_args(watch)

    # probe-booking
    probe = sub.add_parser("probe-booking", help="Deep-probe booking flow for a matched option.")
    _add_provider_arg(probe)
    _add_location_args(probe)
    probe.add_argument("--all-options", action="store_true", dest="all_options",
                       help="Skip semester filter.")
    probe.add_argument("--residence", help="Filter by property name (contains).")
    probe.add_argument("--room", help="Filter by room type (contains).")
    probe.add_argument("--tenancy", help="Filter by tenancy label (contains).")
    probe.add_argument("--index", type=int, default=0, help="Candidate index (default 0).")
    probe.add_argument("--notify", action="store_true", help="Send notification with probe result.")
    probe.add_argument("--json", action="store_true", help="Output JSON.")

    # notify
    notify_cmd = sub.add_parser("notify", help="Send a test notification.")
    notify_cmd.add_argument("--message", help="Message text to send.")

    # test-match (Yugo legacy)
    test_match = sub.add_parser("test-match", help="[Yugo] Test Semester 1 matching logic.")
    test_match.add_argument("--from-year", dest="from_year", type=int, required=True)
    test_match.add_argument("--to-year", dest="to_year", type=int, required=True)
    test_match.add_argument("--name", default="Semester 1")
    test_match.add_argument("--label", default="Semester 1")
    test_match.add_argument("--start-date", dest="start_date", default="2026-09-01")
    test_match.add_argument("--end-date", dest="end_date", default="2027-01-31")
    test_match.add_argument("--json", action="store_true")

    return parser


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    config, warnings = load_config(args.config)
    for warning in warnings:
        logger.warning(warning)

    handlers = {
        "discover": handle_discover,
        "scan": handle_scan,
        "watch": handle_watch,
        "probe-booking": handle_probe_booking,
        "notify": handle_notify,
        "test-match": handle_test_match,
    }

    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2

    return handler(args, config)


if __name__ == "__main__":
    sys.exit(main())
