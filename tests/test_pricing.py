from decimal import Decimal

import pytest

from closing_agent.pricing import PricingError, validate_against_spreadsheet


def test_acceptance_2_every_reference_quote_within_one_percent(services):
    rows = validate_against_spreadsheet(services.matrix, services.settings.root / "data" / "reference_quotes.csv")
    assert len(rows) == 12
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]
    assert max(abs(r.variance_pct) for r in rows) < Decimal("0.1")


def test_volume_and_term_discounts_stack(services):
    q = services.matrix.quote("CRM-PRO", 30, 24)  # 49 * 0.90 (25+ tier) * 0.85 (24 mo)
    assert q.unit_net == Decimal("37.49")
    assert q.monthly_net == Decimal("1124.70")
    assert q.total_net == Decimal("26992.80")
    assert q.vat == Decimal("5398.56")


def test_tier_boundary_is_inclusive(services):
    assert services.matrix.volume_discount("CRM-STD", 9) == 0
    assert services.matrix.volume_discount("CRM-STD", 10) == 5


def test_one_off_items_ignore_term(services):
    q = services.matrix.quote("SVC-ONB", 2, 36)
    assert q.term_discount_pct == 0 and q.monthly_net is None
    assert q.total_net == Decimal("3000.00")


@pytest.mark.parametrize("args", [("NOPE", 1, 12), ("CRM-STD", 0, 12), ("CRM-STD", 5, 18)])
def test_invalid_input_raises(services, args):
    with pytest.raises(PricingError):
        services.matrix.quote(*args)


def test_price_options_offers_upsell_near_next_tier(services):
    opts = services.matrix.price_options("CRM-STD", 8)
    assert [o["term_months"] for o in opts["options"]] == [1, 12, 24, 36]
    assert opts["lowest_unit_price_term"] == 36
    assert opts["volume_upsell"]["quantity"] == 10


def test_no_upsell_when_next_tier_is_far(services):
    assert "volume_upsell" not in services.matrix.price_options("CRM-STD", 12)


def test_compare_flags_discrepancy(services):
    expected = services.matrix.quote("CRM-ENT", 25, 36).total_net
    bad = services.matrix.compare("CRM-ENT", 25, 36, expected * Decimal("0.92"))
    assert not bad["within_tolerance"] and bad["variance_pct"] == "-8.000"
    assert services.matrix.compare("CRM-ENT", 25, 36, expected)["within_tolerance"]


def test_catalog_text_is_deterministic(services):
    assert services.matrix.catalog_text() == services.matrix.catalog_text()
    assert "CRM-ENT" in services.matrix.catalog_text()


def test_compare_accepts_formatted_amounts_and_rejects_garbage(services):
    expected = services.matrix.quote("CRM-STD", 12, 12).total_net  # 3571.20
    assert services.matrix.compare("CRM-STD", 12, 12, "£3,571.20")["within_tolerance"]
    for bad in ("about three grand", "NaN", ""):
        with pytest.raises(PricingError):
            services.matrix.compare("CRM-STD", 12, 12, bad)
    assert expected == Decimal("3571.20")
