"""
providers/base.py — Abstract base class for accommodation providers.
All providers return normalised RoomOption objects so the CLI can
treat Yugo and Aparto (or any future provider) uniformly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from student_rooms.models.config import AcademicYearConfig


@dataclass
class RoomOption:
    """Normalised room option returned by every provider."""
    provider: str                       # "yugo" | "aparto"
    property_name: str
    property_slug: str                  # URL slug / short identifier
    room_type: str                      # e.g. "Gold Ensuite", "Deluxe Studio"
    price_weekly: Optional[float]       # Weekly price in EUR (None if unknown)
    price_label: str                    # Raw human-readable price string
    available: bool                     # True = currently appears bookable
    booking_url: Optional[str]          # Direct booking link (if known)
    start_date: Optional[str]           # ISO date string YYYY-MM-DD
    end_date: Optional[str]             # ISO date string YYYY-MM-DD
    academic_year: Optional[str]        # e.g. "2026-27"
    option_name: Optional[str]          # Tenancy / option label
    location: Optional[str] = None      # Human-readable location hint
    raw: Dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Stable key used for deduplication across watch cycles."""
        return "|".join([
            self.provider,
            self.property_slug,
            self.room_type.lower().strip(),
            self.academic_year or "",
            self.option_name or "",
        ])

    def alert_lines(self) -> List[str]:
        """Human-readable summary lines for alerts."""
        price_str = f"€{self.price_weekly:.0f}/week" if self.price_weekly else self.price_label or "N/A"
        lines = [
            f"🏠 {self.property_name} ({self.provider.upper()})",
            f"🛏 {self.room_type}",
            f"💶 {price_str}",
        ]
        if self.start_date or self.end_date:
            lines.append(f"📅 {self.start_date or '?'} → {self.end_date or '?'}")
        if self.option_name:
            lines.append(f"📋 {self.option_name}")
        if self.location:
            lines.append(f"📍 {self.location}")
        if self.booking_url:
            lines.append(f"🔗 {self.booking_url}")
        return lines


class BaseProvider(ABC):
    """Abstract provider interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short provider name, e.g. 'yugo' or 'aparto'."""
        ...

    @abstractmethod
    def discover_properties(self) -> List[Dict[str, Any]]:
        """Return raw property metadata for the discover command."""
        ...

    @abstractmethod
    def scan(
        self,
        academic_year: str = "2026-27",
        semester: int = 1,
        apply_semester_filter: bool = True,
        academic_config: Optional[AcademicYearConfig] = None,
    ) -> List[RoomOption]:
        """
        Scan for available room options.
        Returns a list of RoomOption matching the given criteria.
        """
        ...

    def probe_booking(self, option: RoomOption) -> Dict[str, Any]:
        """
        Deep-probe the booking flow for a given option.
        Returns a dict with booking context + links.
        Providers that don't implement this raise NotImplementedError.
        """
        raise NotImplementedError(f"Provider '{self.name}' does not implement probe_booking.")
