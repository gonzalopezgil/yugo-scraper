import unittest

from student_rooms.matching import apply_filters, match_semester1
from student_rooms.models.config import AcademicYearConfig, FilterConfig, Semester1Rules
from student_rooms.providers.base import RoomOption


class TestSemester1Matching(unittest.TestCase):
    def _cfg(self):
        return AcademicYearConfig(
            start_year=2026,
            end_year=2027,
            semester1=Semester1Rules(
                name_keywords=["semester 1"],
                require_keyword=True,
                enforce_month_window=True,
                start_months=[9, 10],
                end_months=[1, 2],
            ),
        )

    def test_semester1_match(self):
        config = self._cfg()
        option = {
            "fromYear": 2026,
            "toYear": 2027,
            "tenancyOption": [
                {
                    "name": "Semester 1",
                    "formattedLabel": "Semester 1",
                    "startDate": "2026-09-15",
                    "endDate": "2027-01-20",
                }
            ],
        }
        self.assertTrue(match_semester1(option, config))

    def test_semester1_mismatch_year(self):
        config = self._cfg()
        option = {
            "fromYear": 2025,
            "toYear": 2026,
            "tenancyOption": [
                {
                    "name": "Semester 1",
                    "formattedLabel": "Semester 1",
                    "startDate": "2025-09-20",
                    "endDate": "2026-01-25",
                }
            ],
        }
        self.assertFalse(match_semester1(option, config))

    def test_semester1_mismatch_keyword(self):
        config = self._cfg()
        option = {
            "fromYear": 2026,
            "toYear": 2027,
            "tenancyOption": [
                {
                    "name": "41 Weeks",
                    "formattedLabel": "THU, 27 AUG 2026 - THU, 10 JUN 2027",
                    "startDate": "2026-08-27",
                    "endDate": "2027-06-10",
                }
            ],
        }
        self.assertFalse(match_semester1(option, config))

    def test_semester1_mismatch_month_window(self):
        config = self._cfg()
        option = {
            "fromYear": 2026,
            "toYear": 2027,
            "tenancyOption": [
                {
                    "name": "Semester 1",
                    "formattedLabel": "Semester 1",
                    "startDate": "2026-08-27",
                    "endDate": "2027-06-10",
                }
            ],
        }
        self.assertFalse(match_semester1(option, config))


class TestFilterPipeline(unittest.TestCase):
    def _option(self, **overrides):
        room_data = {
            "soldOut": False,
            "bathroomArrangement": "Private",
            "kitchenArrangement": "Shared",
            "priceLabel": "€200 per week",
            "minPriceForBillingCycle": 200,
        }
        defaults = {
            "provider": "yugo",
            "property_name": "Test Hall",
            "property_slug": "test-hall",
            "room_type": "Ensuite",
            "price_weekly": 200.0,
            "price_label": "€200/week",
            "available": True,
            "booking_url": None,
            "start_date": "2026-09-01",
            "end_date": "2027-01-31",
            "academic_year": "2026-27",
            "option_name": "Semester 1",
            "location": "Test City",
            "raw": {"roomData": room_data},
        }
        defaults.update(overrides)
        return RoomOption(**defaults)

    def test_filters_exclude_by_price(self):
        opt = self._option(price_weekly=220.0)
        filters = FilterConfig(max_weekly_price=200.0)
        results = apply_filters([opt], filters)
        self.assertEqual(results, [])

    def test_filters_exclude_by_private_bathroom(self):
        opt = self._option()
        filters = FilterConfig(private_bathroom=False)
        results = apply_filters([opt], filters)
        self.assertEqual(results, [])

    def test_filters_exclude_by_private_kitchen(self):
        opt = self._option()
        filters = FilterConfig(private_kitchen=True)
        results = apply_filters([opt], filters)
        self.assertEqual(results, [])

    def test_filters_allow_matching(self):
        opt = self._option()
        filters = FilterConfig(max_weekly_price=250.0, private_bathroom=True)
        results = apply_filters([opt], filters)
        self.assertEqual(len(results), 1)

    def test_filters_exclude_by_monthly_price(self):
        opt = self._option(price_weekly=220.0)
        filters = FilterConfig(max_monthly_price=800.0)
        results = apply_filters([opt], filters)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
