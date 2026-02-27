"""
providers/yugo.py — Yugo accommodation provider.
Wraps the original YugoClient API and adapts output to RoomOption.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from student_rooms.matching import match_semester1
from student_rooms.models.config import AcademicYearConfig
from student_rooms.providers.base import BaseProvider, RoomOption

logger = logging.getLogger(__name__)

API_PREFIX = "https://yugo.com/en-gb/"

# ---------------------------------------------------------------------------
# Low-level API client
# ---------------------------------------------------------------------------

class YugoClient:
    def __init__(
        self,
        session: Optional[requests.Session] = None,
        base_url: str = API_PREFIX,
        timeout: int = 30,
        retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ):
        self.session = session or requests.Session()
        self.base_url = base_url
        self.timeout = timeout
        self.retries = max(1, retries)
        self.retry_backoff_seconds = max(0.1, retry_backoff_seconds)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self.base_url + path
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.request(
                    method.upper(), url, params=params, data=data, timeout=self.timeout
                )
                # Client errors (4xx) are not retryable — raise immediately
                if 400 <= response.status_code < 500:
                    response.raise_for_status()
                # Server errors (5xx) are transient — retry
                if response.status_code >= 500:
                    response.raise_for_status()
                try:
                    return response.json()
                except ValueError as exc:
                    raise ValueError(
                        f"Yugo API returned non-JSON response for {method} {path} "
                        f"(HTTP {response.status_code})"
                    ) from exc
            except requests.RequestException as exc:
                last_error = exc
                # Don't retry client errors (4xx)
                if hasattr(exc, 'response') and exc.response is not None and 400 <= exc.response.status_code < 500:
                    raise
                if attempt >= self.retries:
                    raise
                sleep_for = self.retry_backoff_seconds * attempt
                logger.warning(
                    "Yugo API request failed (%s %s): %s [retry %s/%s in %.1fs]",
                    method, path, exc, attempt, self.retries, sleep_for,
                )
                time.sleep(sleep_for)

        if last_error:
            raise last_error
        raise RuntimeError(f"Unexpected request failure for {method} {path}")

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request_json("GET", path, params=params)

    def _post_json(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json("POST", path, data=data)

    def list_countries(self) -> List[Dict[str, Any]]:
        data = self._get_json("countries")
        return data.get("countries", [])

    def list_cities(self, country_id: str) -> List[Dict[str, Any]]:
        data = self._get_json("cities", params={"countryId": country_id})
        return data.get("cities", [])

    def list_residences(self, city_id: str) -> List[Dict[str, Any]]:
        data = self._get_json("residences", params={"cityId": city_id})
        return data.get("residences", [])

    def list_rooms(self, residence_id: str) -> List[Dict[str, Any]]:
        data = self._get_json("rooms", params={"residenceId": residence_id})
        return data.get("rooms", [])

    def list_tenancy_options(
        self, residence_id: str, residence_content_id: str, room_id: str
    ) -> List[Dict[str, Any]]:
        data = self._get_json(
            "tenancyOptionsBySSId",
            params={
                "residenceId": residence_id,
                "residenceContentId": residence_content_id,
                "roomId": room_id,
            },
        )
        return data.get("tenancy-options", [])

    # Booking-flow endpoints
    def get_residence_property(self, residence_id: str) -> Dict[str, Any]:
        return self._get_json("residence-property", params={"residenceId": residence_id})

    def get_available_beds(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._get_json("available-beds", params=params)

    def get_flats_with_beds(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._get_json("flats-with-beds", params=params)

    def get_skip_room_selection(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._get_json("skip-room-selection", params=params)

    def post_student_portal_redirect(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json("student-portal-redirect", data=data)


def find_by_name(items: List[Dict[str, Any]], name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    target = name.strip().lower()
    for item in items:
        if str(item.get("name", "")).strip().lower() == target:
            return item
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_private_arrangement(room: Dict, key: str) -> Optional[bool]:
    arrangement = room.get(key)
    if arrangement:
        return "private" in arrangement.lower()
    return None


def is_ensuite(room: Dict) -> bool:
    return bool(_has_private_arrangement(room, "bathroomArrangement"))


def get_weekly_price(room: Dict) -> Optional[float]:
    price_label = room.get("priceLabel")
    if price_label:
        label = price_label.lower()
        price_billing_cycle = room.get("minPriceForBillingCycle")
        if price_billing_cycle is not None:
            try:
                if "week" in label:
                    return float(price_billing_cycle)
                if "month" in label:
                    return float(price_billing_cycle) / 4.33
            except (TypeError, ValueError):
                return None
    price_per_night = room.get("minPricePerNight")
    if price_per_night:
        try:
            return float(price_per_night) * 7
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------

class YugoProvider(BaseProvider):
    """Yugo provider: uses the official (undocumented) Yugo JSON API."""

    def __init__(
        self,
        country: str = "Ireland",
        city: str = "Dublin",
        country_id: Optional[str] = None,
        city_id: Optional[str] = None,
    ):
        self._client = YugoClient()
        self._country = country
        self._city = city
        self._country_id = country_id
        self._city_id = city_id

    @property
    def name(self) -> str:
        return "yugo"

    def _resolve_city_id(self) -> Optional[str]:
        if self._city_id:
            return self._city_id

        cid = self._resolve_country_id()
        if not cid:
            return None

        cities = self._client.list_cities(cid)
        city_match = find_by_name(cities, self._city)
        if not city_match:
            logger.error("Yugo: city '%s' not found", self._city)
            return None

        return str(city_match.get("contentId") or city_match.get("id") or "")

    def _resolve_country_id(self) -> Optional[str]:
        if self._country_id:
            return str(self._country_id)

        countries = self._client.list_countries()
        country_match = find_by_name(countries, self._country)
        if not country_match:
            logger.error("Yugo: country '%s' not found", self._country)
            return None
        return str(country_match.get("countryId") or country_match.get("id") or "")

    def discover_properties(self) -> List[Dict[str, Any]]:
        city_id = self._resolve_city_id()
        if not city_id:
            return []
        return self._client.list_residences(city_id)

    def list_countries(self) -> List[Dict[str, Any]]:
        return self._client.list_countries()

    def list_cities(self, country_id: Optional[str] = None) -> List[Dict[str, Any]]:
        cid = country_id or self._resolve_country_id()
        if not cid:
            return []
        return self._client.list_cities(cid)

    def list_residences(self, city_id: Optional[str] = None) -> List[Dict[str, Any]]:
        cid = city_id or self._resolve_city_id()
        if not cid:
            return []
        return self._client.list_residences(cid)

    def _academic_year_matches(
        self,
        group: Dict[str, Any],
        academic_year: str,
        semester: int,
    ) -> bool:
        """Check if a tenancy group belongs to the target academic year / semester."""
        from_year = group.get("fromYear")
        to_year = group.get("toYear")
        try:
            start_year, end_year = (int(y) for y in academic_year.split("-"))
            if end_year < 100:
                end_year = (from_year // 100) * 100 + end_year if from_year else end_year + 2000
        except (ValueError, AttributeError):
            return True

        if from_year is not None and int(from_year) != start_year:
            return False
        if to_year is not None and int(to_year) != end_year:
            return False
        return True

    def scan(
        self,
        academic_year: str = "2026-27",
        semester: int = 1,
        apply_semester_filter: bool = True,
        academic_config: Optional[AcademicYearConfig] = None,
    ) -> List[RoomOption]:
        city_id = self._resolve_city_id()
        if not city_id:
            return []

        if academic_config is None:
            academic_config = AcademicYearConfig()
            try:
                start_year, end_year = (int(y) for y in academic_year.split("-"))
                if end_year < 100:
                    end_year = (start_year // 100) * 100 + end_year
                academic_config.start_year = start_year
                academic_config.end_year = end_year
            except (ValueError, AttributeError):
                pass

        results: List[RoomOption] = []
        residences = self._client.list_residences(city_id)

        for residence in residences:
            residence_id = residence.get("id")
            residence_content_id = residence.get("contentId")
            if not residence_id or not residence_content_id:
                continue

            rooms = self._client.list_rooms(str(residence_id))
            for room in rooms:
                if room.get("soldOut") is not False:
                    continue

                room_id = room.get("id")
                if not room_id:
                    continue

                groups = self._client.list_tenancy_options(
                    str(residence_id), str(residence_content_id), str(room_id)
                )
                if not groups:
                    continue

                for group in groups:
                    if apply_semester_filter and not self._academic_year_matches(group, academic_year, semester):
                        continue

                    options = group.get("tenancyOption") or []
                    for option in options:
                        if apply_semester_filter and semester == 1:
                            option_payload = {
                                "fromYear": group.get("fromYear"),
                                "toYear": group.get("toYear"),
                                "tenancyOption": [{
                                    "name": option.get("name"),
                                    "formattedLabel": option.get("formattedLabel"),
                                    "startDate": option.get("startDate"),
                                    "endDate": option.get("endDate"),
                                }],
                            }
                            if not match_semester1(option_payload, academic_config):
                                continue

                        weekly = get_weekly_price(room)
                        price_label = room.get("priceLabel") or ""

                        results.append(RoomOption(
                            provider="yugo",
                            property_name=residence.get("name") or "",
                            property_slug=str(residence_id),
                            room_type=room.get("name") or "",
                            price_weekly=weekly,
                            price_label=f"€{weekly:.0f}/week" if weekly else price_label,
                            available=True,
                            booking_url=(
                                option.get("linkToRedirect")
                                or residence.get("portalLink")
                                or residence.get("paymentLink")
                            ),
                            start_date=option.get("startDate"),
                            end_date=option.get("endDate"),
                            academic_year=academic_year,
                            option_name=option.get("name") or option.get("formattedLabel"),
                            location=residence.get("locationInfo"),
                            raw={
                                "residenceId": str(residence_id),
                                "residenceContentId": str(residence_content_id),
                                "roomId": str(room_id),
                                "optionId": str(option.get("id")) if option.get("id") else None,
                                "academicYearId": group.get("academicYearId"),
                                "fromYear": group.get("fromYear"),
                                "toYear": group.get("toYear"),
                                "roomData": room,
                                "residencePortalLink": residence.get("portalLink"),
                                "residencePaymentLink": residence.get("paymentLink"),
                                "maxNumOfBedsInFlat": room.get("maxNumOfBedsInFlat"),
                                "optionLinkToRedirect": option.get("linkToRedirect"),
                                "optionStartDate": option.get("startDate"),
                                "optionEndDate": option.get("endDate"),
                                "optionTenancyLength": option.get("tenancyLength"),
                                "optionStatus": option.get("status"),
                            },
                        ))

        return results

    def probe_booking(self, option: RoomOption) -> Dict[str, Any]:
        """Deep-probe the Yugo booking flow for a given option."""
        from datetime import datetime

        raw = option.raw
        residence_content_id = raw.get("residenceContentId") or ""

        # Warm booking session
        self._client.session.get(
            self._client.base_url + "booking-flow-page",
            params={"residenceContentId": residence_content_id},
            timeout=self._client.timeout,
        ).raise_for_status()

        property_data = self._client.get_residence_property(raw["residenceId"])
        buildings = ((property_data.get("property") or {}).get("buildings") or [])
        building_ids = [b.get("id") for b in buildings if b.get("id")]

        floor_indexes_set = set()
        for building in buildings:
            for floor in building.get("floors") or []:
                try:
                    floor_indexes_set.add(int(float(floor.get("index"))))
                except (TypeError, ValueError):
                    continue
        floor_indexes = sorted(floor_indexes_set)

        if not building_ids or not floor_indexes:
            raise RuntimeError("Could not resolve building/floor metadata for booking probe.")

        def _to_js_date(date_str: str) -> str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%a %b %d %Y 00:00:00 GMT+0000 (UTC)")

        start_date_raw = option.start_date or raw.get("optionStartDate")
        end_date_raw = option.end_date or raw.get("optionEndDate")
        if not start_date_raw or not end_date_raw:
            raise RuntimeError("Missing tenancy start/end dates for booking probe.")

        start_date_js = _to_js_date(start_date_raw)
        end_date_js = _to_js_date(end_date_raw)

        common_params = {
            "roomTypeId": raw["roomId"],
            "residenceExternalId": raw["residenceId"],
            "tenancyOptionId": raw["optionId"],
            "tenancyStartDate": start_date_js,
            "tenancyEndDate": end_date_js,
            "academicYearId": raw.get("academicYearId"),
            "maxNumOfFlatmates": str(raw.get("maxNumOfBedsInFlat") or 7),
            "buildingIds": ",".join(building_ids),
            "floorIndexes": ",".join(str(i) for i in floor_indexes),
        }

        available_beds = self._client.get_available_beds(common_params)
        room = raw.get("roomData") or {}
        flats_with_beds = self._client.get_flats_with_beds({
            **common_params,
            "sortDirection": "false",
            "pageNumber": "1",
            "pageSize": "6",
            "totalPriceOriginal": "0",
            "pricePerNightOriginal": str(room.get("minPricePerNight") or ""),
        })

        selected_bed_id = None
        selected_flat_id = None
        floors = ((flats_with_beds.get("flats") or {}).get("floors") or [])
        for floor in floors:
            for flat in floor.get("flats") or []:
                beds = flat.get("beds") or []
                if beds:
                    selected_bed_id = beds[0].get("bedId") or beds[0].get("id")
                    selected_flat_id = flat.get("id")
                    break
            if selected_bed_id:
                break

        skip_room = self._client.get_skip_room_selection(common_params)
        handover = self._client.post_student_portal_redirect({
            "roomTypeId": raw["roomId"],
            "residenceExternalId": raw["residenceId"],
            "tenancyOptionId": raw["optionId"],
            "tenancyStartDate": start_date_js,
            "tenancyEndDate": end_date_js,
            "academicYearId": raw.get("academicYearId"),
            "bedId": selected_bed_id or "",
            "flatId": selected_flat_id or "",
            "currencyCode": "EUR",
        })

        return {
            "match": {
                "residence": option.property_name,
                "room": option.room_type,
                "tenancy": option.option_name,
                "fromYear": raw.get("fromYear"),
                "toYear": raw.get("toYear"),
                "startDate": option.start_date,
                "endDate": option.end_date,
                "weeklyPrice": option.price_weekly,
            },
            "bookingContext": {
                "commonParams": common_params,
                "selectedBedId": selected_bed_id,
                "selectedFlatId": selected_flat_id,
            },
            "apiResults": {
                "availableBeds": available_beds,
                "flatsWithBedsSummary": {
                    "floorsReturned": len(floors),
                },
                "skipRoomSelection": skip_room,
                "studentPortalRedirect": handover,
            },
            "links": {
                "skipRoomLink": skip_room.get("linkToRedirect"),
                "handoverLink": handover.get("linkToRedirect"),
                "portalLink": raw.get("residencePortalLink"),
                "paymentLink": raw.get("residencePaymentLink"),
            },
        }
