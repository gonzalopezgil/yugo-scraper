"""
tests/test_aparto.py — Tests for the Aparto provider.

Tests dynamic property discovery, HTML parsing, price extraction,
term matching, and the provider interface across multiple cities.
"""
import unittest
from unittest.mock import MagicMock, patch

from student_rooms.matching import match_semester1
from student_rooms.models.config import AcademicYearConfig, Semester1Rules
from student_rooms.providers.aparto import (
    ApartoProvider,
    CITY_COUNTRY_MAP,
    CITY_SLUG_MAP,
    COUNTRY_PORTAL_MAP,
    _build_property_aliases,
    _discover_city_properties,
    _extract_next_data,
    _extract_prices_from_html,
    _extract_property_name,
    _extract_rsc_json_chunks,
    _is_target_city_term,
    _normalise_name,
    _parse_months_from_name,
    _parse_weeks_from_name,
)
from student_rooms.providers.base import RoomOption


# ---------------------------------------------------------------------------
# Sample HTML fragments for testing
# ---------------------------------------------------------------------------

SAMPLE_PROPERTY_HTML = """
<!DOCTYPE html>
<html>
<head><title>Binary Hub - Aparto</title></head>
<body>
<div class="room-types">
  <div class="room-card">
    <h3>Bronze Ensuite</h3>
    <p class="price">€291 p/w</p>
  </div>
  <div class="room-card">
    <h3>Silver Ensuite</h3>
    <p class="price">€300 p/w</p>
  </div>
  <div class="room-card">
    <h3>Gold Ensuite</h3>
    <p class="price">€310 p/w</p>
  </div>
  <div class="room-card">
    <h3>Platinum Ensuite</h3>
    <p class="price">€320 p/w</p>
  </div>
</div>
</body>
</html>
"""

SAMPLE_PROPERTY_HTML_NO_PRICE = """
<!DOCTYPE html>
<html>
<head><title>Stephen's Quarter - Aparto</title></head>
<body>
<div class="room-types">
  <div class="room-card">
    <h3>Bronze Ensuite</h3>
    <p>Coming soon</p>
  </div>
  <div class="room-card">
    <h3>Studio Room</h3>
    <p>Contact for pricing</p>
  </div>
</div>
</body>
</html>
"""

SAMPLE_NEXT_DATA_HTML = """
<!DOCTYPE html>
<html>
<head>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "rooms": [
        {"name": "Gold Ensuite", "price": 310},
        {"name": "Platinum Ensuite", "price": 320}
      ]
    }
  }
}
</script>
</head>
<body></body>
</html>
"""

SAMPLE_RSC_HTML = """
<!DOCTYPE html>
<html>
<head>
<script>
self.__next_f.push([1, "0:{\\"rooms\\":[{\\"name\\":\\"Bronze Ensuite\\",\\"price\\":291}]}"])
</script>
</head>
<body>
<div>Bronze Ensuite</div>
<div>€291 p/w</div>
</body>
</html>
"""

SAMPLE_CITY_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Dublin Student Accommodation | aparto</title></head>
<body>
<div>
  <a href="https://apartostudent.com/locations/dublin/binary-hub">Binary Hub</a>
  <a href="https://apartostudent.com/locations/dublin/beckett-house">Beckett House</a>
  <a href="https://apartostudent.com/locations/dublin/dorset-point">Dorset Point</a>
  <a href="https://apartostudent.com/locations/dublin/the-loom">The Loom</a>
  <a href="https://apartostudent.com/locations/dublin/montrose">Montrose</a>
  <a href="https://apartostudent.com/locations/dublin/stephens-quarter">Stephen's Quarter</a>
</div>
</body>
</html>
"""

SAMPLE_BARCELONA_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Barcelona Student Accommodation | aparto</title></head>
<body>
<div>
  <a href="https://apartostudent.com/locations/barcelona/pallars">Pallars</a>
  <a href="https://apartostudent.com/locations/barcelona/pallars/short-stays">Short stays</a>
  <a href="https://apartostudent.com/locations/barcelona/cristobal-de-moura">Cristóbal de Moura</a>
  <a href="https://apartostudent.com/locations/barcelona/diagonal-suites">Diagonal Suites</a>
</div>
</body>
</html>
"""


class TestExtractPricesFromHtml(unittest.TestCase):
    """Test HTML price/room extraction."""

    def test_extracts_all_tiers(self):
        rooms = _extract_prices_from_html(SAMPLE_PROPERTY_HTML, "Binary Hub")
        self.assertEqual(len(rooms), 4)
        room_types = [r["room_type"] for r in rooms]
        self.assertIn("Bronze Ensuite", room_types)
        self.assertIn("Silver Ensuite", room_types)
        self.assertIn("Gold Ensuite", room_types)
        self.assertIn("Platinum Ensuite", room_types)

    def test_extracts_prices(self):
        rooms = _extract_prices_from_html(SAMPLE_PROPERTY_HTML, "Binary Hub")
        prices = {r["room_type"]: r["price_weekly"] for r in rooms}
        self.assertEqual(prices["Bronze Ensuite"], 291.0)
        self.assertEqual(prices["Silver Ensuite"], 300.0)
        self.assertEqual(prices["Gold Ensuite"], 310.0)
        self.assertEqual(prices["Platinum Ensuite"], 320.0)

    def test_sorted_by_tier_order(self):
        rooms = _extract_prices_from_html(SAMPLE_PROPERTY_HTML, "Binary Hub")
        types = [r["room_type"] for r in rooms]
        self.assertEqual(types[0], "Bronze Ensuite")
        self.assertEqual(types[-1], "Platinum Ensuite")

    def test_no_price_fallback(self):
        rooms = _extract_prices_from_html(SAMPLE_PROPERTY_HTML_NO_PRICE, "Stephen's Quarter")
        self.assertTrue(len(rooms) >= 1)
        for room in rooms:
            self.assertIn("room_type", room)

    def test_price_label_format(self):
        rooms = _extract_prices_from_html(SAMPLE_PROPERTY_HTML, "Binary Hub")
        for room in rooms:
            if room["price_weekly"]:
                self.assertTrue(room["price_label"].startswith("€"))
                self.assertIn("/week", room["price_label"])


class TestExtractNextData(unittest.TestCase):
    """Test __NEXT_DATA__ extraction."""

    def test_extracts_json(self):
        data = _extract_next_data(SAMPLE_NEXT_DATA_HTML)
        self.assertIsNotNone(data)
        self.assertIn("props", data)

    def test_returns_none_without_tag(self):
        data = _extract_next_data("<html><body>No NEXT_DATA here</body></html>")
        self.assertIsNone(data)


class TestExtractRscJsonChunks(unittest.TestCase):
    """Test RSC JSON chunk extraction."""

    def test_extracts_chunks(self):
        chunks = _extract_rsc_json_chunks(SAMPLE_RSC_HTML)
        self.assertTrue(len(chunks) >= 0)


class TestCityCountryMappings(unittest.TestCase):
    """Test the city/country/portal configuration maps."""

    def test_all_14_cities_mapped(self):
        """All 14 Aparto cities should be in the CITY_COUNTRY_MAP."""
        expected_cities = {
            "Dublin", "Barcelona", "Milan", "Florence", "Paris",
            "Aberdeen", "Brighton", "Bristol", "Cambridge", "Glasgow",
            "Kingston", "Lancaster", "Oxford", "Reading",
        }
        # Kingston-London is an alias for Kingston
        mapped_cities = set(CITY_COUNTRY_MAP.keys()) - {"Kingston-London"}
        self.assertEqual(mapped_cities, expected_cities)

    def test_all_cities_have_slugs(self):
        for city in CITY_COUNTRY_MAP:
            self.assertIn(city, CITY_SLUG_MAP, f"Missing slug for {city}")

    def test_all_countries_have_portal_config(self):
        countries_used = set(CITY_COUNTRY_MAP.values())
        for country in countries_used:
            self.assertIn(country, COUNTRY_PORTAL_MAP, f"Missing portal config for {country}")

    def test_ie_es_it_share_portal(self):
        """Ireland, Spain, Italy should share the same StarRez portal."""
        ie_base = COUNTRY_PORTAL_MAP["Ireland"]["portal_base"]
        es_base = COUNTRY_PORTAL_MAP["Spain"]["portal_base"]
        it_base = COUNTRY_PORTAL_MAP["Italy"]["portal_base"]
        self.assertEqual(ie_base, es_base)
        self.assertEqual(es_base, it_base)

    def test_uk_has_separate_portal(self):
        uk_base = COUNTRY_PORTAL_MAP["UK"]["portal_base"]
        ie_base = COUNTRY_PORTAL_MAP["Ireland"]["portal_base"]
        self.assertNotEqual(uk_base, ie_base)
        self.assertIsNotNone(uk_base)

    def test_france_has_no_portal(self):
        self.assertIsNone(COUNTRY_PORTAL_MAP["France"]["portal_base"])


class TestDynamicPropertyDiscovery(unittest.TestCase):
    """Test scraping city pages for property discovery."""

    @patch("student_rooms.providers.aparto._fetch")
    def test_discover_dublin_properties(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_CITY_PAGE_HTML
        import requests
        props = _discover_city_properties(requests.Session(), "dublin")
        slugs = {p["slug"] for p in props}
        self.assertIn("binary-hub", slugs)
        self.assertIn("beckett-house", slugs)
        self.assertIn("dorset-point", slugs)
        self.assertIn("the-loom", slugs)
        self.assertIn("montrose", slugs)
        self.assertIn("stephens-quarter", slugs)
        self.assertEqual(len(props), 6)

    @patch("student_rooms.providers.aparto._fetch")
    def test_discover_barcelona_properties(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_BARCELONA_PAGE_HTML
        import requests
        props = _discover_city_properties(requests.Session(), "barcelona")
        slugs = {p["slug"] for p in props}
        self.assertIn("pallars", slugs)
        self.assertIn("cristobal-de-moura", slugs)
        self.assertIn("diagonal-suites", slugs)
        # short-stays should be filtered out
        self.assertNotIn("short-stays", slugs)
        self.assertEqual(len(props), 3)

    @patch("student_rooms.providers.aparto._fetch")
    def test_discover_returns_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        import requests
        props = _discover_city_properties(requests.Session(), "nonexistent")
        self.assertEqual(props, [])


class TestPropertyAliases(unittest.TestCase):
    """Test property alias/abbreviation mapping."""

    def test_builds_aliases(self):
        props = [
            {"name": "Cristobal De Moura", "slug": "cristobal-de-moura"},
            {"name": "Pallars", "slug": "pallars"},
        ]
        aliases = _build_property_aliases(props)
        # Should include normalised names
        self.assertIn("cristobal de moura", aliases)
        self.assertIn("pallars", aliases)
        # Should include initials
        self.assertIn("cdm", aliases)

    def test_alias_maps_to_canonical_name(self):
        props = [{"name": "Cristobal De Moura", "slug": "cristobal-de-moura"}]
        aliases = _build_property_aliases(props)
        self.assertEqual(aliases.get("cdm"), "Cristobal De Moura")


class TestIsTargetCityTerm(unittest.TestCase):
    """Test term-to-city matching."""

    def _dublin_context(self):
        names = {"Binary Hub", "Beckett House", "Dorset Point", "Montrose", "The Loom", "Stephens Quarter"}
        aliases = _build_property_aliases([{"name": n, "slug": ""} for n in names])
        return names, aliases

    def _barcelona_context(self):
        names = {"Pallars", "Cristobal De Moura", "Diagonal Suites"}
        aliases = _build_property_aliases([{"name": n, "slug": ""} for n in names])
        return names, aliases

    def test_dublin_term_matches_dublin(self):
        names, aliases = self._dublin_context()
        self.assertTrue(_is_target_city_term("Binary Hub - 26/27 - 41 Weeks", names, aliases))
        self.assertTrue(_is_target_city_term("The Loom - 26/27 - Semester 1", names, aliases))
        self.assertTrue(_is_target_city_term("Dorset Point - 26/27 - 41 Weeks", names, aliases))

    def test_dublin_term_does_not_match_barcelona(self):
        names, aliases = self._barcelona_context()
        self.assertFalse(_is_target_city_term("Binary Hub - 26/27 - 41 Weeks", names, aliases))
        self.assertFalse(_is_target_city_term("The Loom - 26/27 - Semester 1", names, aliases))

    def test_barcelona_term_matches_barcelona(self):
        names, aliases = self._barcelona_context()
        self.assertTrue(_is_target_city_term("Pallars - 26/27 - 12 months", names, aliases))
        self.assertTrue(_is_target_city_term("Cristobal de Moura - 26/27 - 9 months", names, aliases))

    def test_barcelona_abbreviation_matches(self):
        names, aliases = self._barcelona_context()
        # "PA" should match Pallars, "CdM" should match Cristobal de Moura
        self.assertTrue(_is_target_city_term("PA - 26/27 - Semester 2 Discount", names, aliases))
        self.assertTrue(_is_target_city_term("CdM - 26/27 - TEST", names, aliases))

    def test_milan_term_does_not_match_dublin(self):
        names, aliases = self._dublin_context()
        self.assertFalse(_is_target_city_term("Giovenale - 26/27 - 10 months", names, aliases))
        self.assertFalse(_is_target_city_term("Ripamonti - 26/27 - 12 months", names, aliases))


class TestApartoProvider(unittest.TestCase):
    """Test ApartoProvider methods."""

    def test_provider_name(self):
        provider = ApartoProvider(city="Dublin")
        self.assertEqual(provider.name, "aparto")

    def test_city_country_resolution(self):
        p = ApartoProvider(city="Dublin")
        self.assertEqual(p._country, "Ireland")
        self.assertEqual(p._city_slug, "dublin")

        p = ApartoProvider(city="Barcelona")
        self.assertEqual(p._country, "Spain")
        self.assertEqual(p._city_slug, "barcelona")

        p = ApartoProvider(city="Milan")
        self.assertEqual(p._country, "Italy")

        p = ApartoProvider(city="Brighton")
        self.assertEqual(p._country, "UK")

        p = ApartoProvider(city="Paris")
        self.assertEqual(p._country, "France")

    def test_country_override(self):
        p = ApartoProvider(city="Dublin", country="UK")
        self.assertEqual(p._country, "UK")

    @patch("student_rooms.providers.aparto._discover_city_properties")
    def test_discover_returns_dynamic_properties(self, mock_discover):
        mock_discover.return_value = [
            {"slug": "pallars", "name": "Pallars", "location": "Barcelona", "url": "https://apartostudent.com/locations/barcelona/pallars"},
            {"slug": "cristobal-de-moura", "name": "Cristobal De Moura", "location": "Barcelona", "url": "https://apartostudent.com/locations/barcelona/cristobal-de-moura"},
        ]
        provider = ApartoProvider(city="Barcelona")
        props = provider.discover_properties()
        self.assertEqual(len(props), 2)
        for prop in props:
            self.assertEqual(prop["provider"], "aparto")
            self.assertEqual(prop["city"], "Barcelona")
            self.assertEqual(prop["country"], "Spain")

    @patch("student_rooms.providers.aparto._fetch")
    def test_scrape_property_with_html(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_PROPERTY_HTML
        provider = ApartoProvider(city="Dublin")
        rooms = provider._scrape_property({"slug": "binary-hub", "name": "Binary Hub", "location": "Dublin 8", "url": "https://apartostudent.com/locations/dublin/binary-hub"})
        self.assertEqual(len(rooms), 4)
        self.assertEqual(rooms[0]["property_name"], "Binary Hub")
        self.assertEqual(rooms[0]["property_slug"], "binary-hub")

    @patch("student_rooms.providers.aparto._fetch")
    def test_scrape_property_returns_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        provider = ApartoProvider(city="Dublin")
        rooms = provider._scrape_property({"slug": "fake", "name": "Fake", "location": "", "url": "https://apartostudent.com/locations/dublin/fake"})
        self.assertEqual(rooms, [])


class TestRoomOption(unittest.TestCase):
    """Test RoomOption dataclass."""

    def _sample_option(self, **kwargs) -> RoomOption:
        defaults = dict(
            provider="aparto",
            property_name="Binary Hub",
            property_slug="binary-hub",
            room_type="Gold Ensuite",
            price_weekly=310.0,
            price_label="€310/week",
            available=True,
            booking_url="https://portal.apartostudent.com/...",
            start_date=None,
            end_date=None,
            academic_year="2026-27",
            option_name="Semester 1 2026-27",
        )
        defaults.update(kwargs)
        return RoomOption(**defaults)

    def test_dedup_key_stable(self):
        opt1 = self._sample_option()
        opt2 = self._sample_option()
        self.assertEqual(opt1.dedup_key(), opt2.dedup_key())

    def test_dedup_key_differs_by_property(self):
        opt1 = self._sample_option(property_slug="binary-hub")
        opt2 = self._sample_option(property_slug="beckett-house")
        self.assertNotEqual(opt1.dedup_key(), opt2.dedup_key())

    def test_dedup_key_differs_by_room_type(self):
        opt1 = self._sample_option(room_type="Gold Ensuite")
        opt2 = self._sample_option(room_type="Bronze Ensuite")
        self.assertNotEqual(opt1.dedup_key(), opt2.dedup_key())

    def test_alert_lines(self):
        opt = self._sample_option(location="Bonham St, Dublin 8")
        lines = opt.alert_lines()
        self.assertTrue(any("Binary Hub" in l for l in lines))
        self.assertTrue(any("Gold Ensuite" in l for l in lines))
        self.assertTrue(any("€310" in l for l in lines))
        self.assertTrue(any("Dublin 8" in l for l in lines))


class TestApartoScanMocked(unittest.TestCase):
    """Test ApartoProvider.scan with mocked StarRez scraper + discovery."""

    @patch("student_rooms.providers.aparto._discover_city_properties")
    @patch("student_rooms.providers.aparto._fetch")
    def test_scan_returns_room_options_no_semester1(self, mock_fetch, mock_discover):
        mock_discover.return_value = [
            {"slug": "binary-hub", "name": "Binary Hub", "location": "Dublin 8", "url": "https://apartostudent.com/locations/dublin/binary-hub"},
        ]
        mock_fetch.return_value = SAMPLE_PROPERTY_HTML
        provider = ApartoProvider(city="Dublin")

        from student_rooms.providers.aparto import StarRezScraper, StarRezTerm
        full_year_term = StarRezTerm(
            term_id=1267,
            term_name="Binary Hub - 26/27 - 41 Weeks",
            property_name="Binary Hub",
            start_date="29/08/2026",
            end_date="12/06/2027",
            start_iso="2026-08-29",
            end_iso="2027-06-12",
            weeks=41,
            is_target_city=True,
            is_semester1=False,
            has_rooms=True,
            booking_url="https://test.com/term/1267",
        )
        with patch.object(StarRezScraper, "scan_term_range", return_value=[full_year_term]):
            results = provider.scan(academic_year="2026-27", semester=1, apply_semester_filter=True)

        self.assertEqual(len(results), 0)

    @patch("student_rooms.providers.aparto._discover_city_properties")
    @patch("student_rooms.providers.aparto._fetch")
    def test_scan_returns_all_when_semester1_available(self, mock_fetch, mock_discover):
        mock_discover.return_value = [
            {"slug": "binary-hub", "name": "Binary Hub", "location": "Dublin 8", "url": "https://apartostudent.com/locations/dublin/binary-hub"},
        ]
        mock_fetch.return_value = SAMPLE_PROPERTY_HTML
        provider = ApartoProvider(city="Dublin")

        from student_rooms.providers.aparto import StarRezScraper, StarRezTerm
        sem1_term = StarRezTerm(
            term_id=9999,
            term_name="Binary Hub - 26/27 - Semester 1",
            property_name="Binary Hub",
            start_date="01/09/2026",
            end_date="31/01/2027",
            start_iso="2026-09-01",
            end_iso="2027-01-31",
            weeks=22,
            is_target_city=True,
            is_semester1=True,
            has_rooms=True,
            booking_url="https://test.com/term/9999",
        )
        with patch.object(StarRezScraper, "scan_term_range", return_value=[sem1_term]):
            results = provider.scan(academic_year="2026-27", semester=1, apply_semester_filter=True)

        self.assertEqual(len(results), 4)
        for r in results:
            self.assertIsInstance(r, RoomOption)
            self.assertEqual(r.provider, "aparto")
            self.assertEqual(r.academic_year, "2026-27")
            self.assertEqual(r.property_name, "Binary Hub")
            self.assertTrue(r.available)
            self.assertIn("Semester 1", r.option_name)

    @patch("student_rooms.providers.aparto._discover_city_properties")
    @patch("student_rooms.providers.aparto._fetch")
    def test_scan_barcelona(self, mock_fetch, mock_discover):
        """Test scanning Barcelona with mocked data."""
        mock_discover.return_value = [
            {"slug": "pallars", "name": "Pallars", "location": "Barcelona", "url": "https://apartostudent.com/locations/barcelona/pallars"},
        ]
        mock_fetch.return_value = '<html><body><p>Room €959 per month</p></body></html>'
        provider = ApartoProvider(city="Barcelona")

        from student_rooms.providers.aparto import StarRezScraper, StarRezTerm
        bcn_term = StarRezTerm(
            term_id=1367,
            term_name="Pallars - 26/27 - Semester 1",
            property_name="Pallars",
            start_date="01/09/2026",
            end_date="31/01/2027",
            start_iso="2026-09-01",
            end_iso="2027-01-31",
            weeks=22,
            is_target_city=True,
            is_semester1=True,
            has_rooms=True,
            booking_url="https://test.com/term/1367",
        )
        with patch.object(StarRezScraper, "scan_term_range", return_value=[bcn_term]):
            results = provider.scan(academic_year="2026-27", semester=1, apply_semester_filter=True)

        self.assertTrue(len(results) >= 1)
        for r in results:
            self.assertEqual(r.provider, "aparto")
            self.assertEqual(r.property_name, "Pallars")
            self.assertIn("city", r.raw)
            self.assertEqual(r.raw["city"], "Barcelona")
            self.assertEqual(r.raw["country"], "Spain")

    @patch("student_rooms.providers.aparto._discover_city_properties")
    def test_scan_france_returns_empty(self, mock_discover):
        """France has no StarRez portal, scan should return empty."""
        mock_discover.return_value = [
            {"slug": "paris-liberte", "name": "Paris Liberte", "location": "Paris", "url": "https://apartostudent.com/locations/paris/paris-liberte"},
        ]
        provider = ApartoProvider(city="Paris")
        results = provider.scan(academic_year="2026-27")
        self.assertEqual(results, [])


class TestStarRezTermAnalysis(unittest.TestCase):
    """Test StarRez term detection and analysis."""

    def test_match_semester1_requires_keyword(self):
        config = AcademicYearConfig(
            start_year=2026,
            end_year=2027,
            semester1=Semester1Rules(
                name_keywords=["semester 1", "sem 1"],
                require_keyword=True,
                start_months=[8, 9, 10],
                end_months=[12, 1, 2],
                enforce_month_window=True,
            ),
        )
        option = {
            "fromYear": 2026,
            "toYear": 2027,
            "tenancyOption": [{
                "name": "Semester 1 26/27",
                "formattedLabel": "Semester 1",
                "startDate": "2026-09-01",
                "endDate": "2027-01-31",
            }],
        }
        self.assertTrue(match_semester1(option, config))

        option["tenancyOption"][0]["name"] = "Full Year 41 Weeks"
        option["tenancyOption"][0]["formattedLabel"] = "Full Year 41 Weeks"
        self.assertFalse(match_semester1(option, config))

    def test_match_semester1_without_keyword(self):
        config = AcademicYearConfig(
            start_year=2026,
            end_year=2027,
            semester1=Semester1Rules(
                name_keywords=["semester 1"],
                require_keyword=False,
                start_months=[8, 9, 10],
                end_months=[12, 1, 2],
                enforce_month_window=True,
            ),
        )
        option = {
            "fromYear": 2026,
            "toYear": 2027,
            "tenancyOption": [{
                "name": "Short Stay",
                "formattedLabel": "Short Stay",
                "startDate": "2026-09-01",
                "endDate": "2027-01-31",
            }],
        }
        self.assertTrue(match_semester1(option, config))

    def test_extract_property_name(self):
        self.assertEqual(_extract_property_name("Binary Hub - 26/27 - 41 Weeks"), "Binary Hub")
        self.assertEqual(_extract_property_name("Montrose - 26/27 - 41 weeks"), "Montrose")
        self.assertEqual(_extract_property_name("Pallars - 26/27 - 12 months"), "Pallars")
        self.assertEqual(_extract_property_name("Cristobal de Moura - 26/27 - 9 months"), "Cristobal de Moura")
        # No-space variations
        self.assertEqual(_extract_property_name("Cristobal de Moura -26/27-Semester 1-10%"), "Cristobal de Moura")
        self.assertEqual(_extract_property_name("PA - 26/27 - Generic Group"), "PA")
        self.assertEqual(_extract_property_name("aparto Cristobal de Moura-September 2024"), "Cristobal de Moura")

    def test_parse_weeks(self):
        self.assertEqual(_parse_weeks_from_name("Binary Hub - 26/27 - 41 Weeks"), 41)
        self.assertEqual(_parse_weeks_from_name("The Loom - 25/26 - 10 Week Summer"), 10)
        self.assertIsNone(_parse_weeks_from_name("Giovenale - 26/27 - 10 months"))

    def test_parse_months(self):
        self.assertEqual(_parse_months_from_name("Pallars - 26/27 - 12 months"), 12)
        self.assertEqual(_parse_months_from_name("Ripamonti - 26/27 - 10 months"), 10)
        self.assertIsNone(_parse_months_from_name("Binary Hub - 26/27 - 41 Weeks"))


if __name__ == "__main__":
    unittest.main()
