import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import credit_check as cc  # noqa: E402
import odoo_source as osrc  # noqa: E402

TODAY = date(2026, 11, 3)

PARTNERS = {
    1: {"id": 1, "name": "Cafe One", "commercial_partner_id": [1, "Cafe One"], "credit_limit": 3000.0,
        "property_payment_term_id": [2, "15 Days"], "eh_cash_tolerance_override": False},
    2: {"id": 2, "name": "Cafe One, Ahmed", "commercial_partner_id": [1, "Cafe One"], "credit_limit": 0.0,
        "property_payment_term_id": False, "eh_cash_tolerance_override": False},
    3: {"id": 3, "name": "Cash Shop", "commercial_partner_id": [3, "Cash Shop"], "credit_limit": 0.0,
        "property_payment_term_id": [1, "Immediate Payment"], "eh_cash_tolerance_override": True,
        "eh_cash_tolerance_amount": 100.0, "eh_cash_tolerance_days": 0},
    4: {"id": 4, "name": "ZZ TEST Kitchen", "commercial_partner_id": [4, "ZZ TEST Kitchen"], "credit_limit": 500.0,
        "property_payment_term_id": False, "eh_cash_tolerance_override": False},
}
LINES = {
    124: [
        {"partner_id": [2, "Cafe One, Ahmed"], "move_name": "INV/1", "date": "2026-09-01",
         "date_maturity": "2026-09-16", "amount_residual": 800.0},
        {"partner_id": [1, "Cafe One"], "move_name": "INV/2", "date": "2026-10-25",
         "date_maturity": "2026-11-09", "amount_residual": 400.0},
        {"partner_id": [3, "Cash Shop"], "move_name": "INV/3", "date": "2026-10-30",
         "date_maturity": "2026-10-30", "amount_residual": 60.0},
    ],
    3: [{"partner_id": [2, "Cafe One, Ahmed"], "move_name": "OLD/1", "date": "2026-06-01",
         "date_maturity": "2026-06-01", "amount_residual": 250.0}],
}


class FakeClient:
    url = "https://odoo.test"

    def __init__(self):
        self.calls = []

    def call(self, model, method, **kw):
        self.calls.append((model, method))
        assert method in osrc.READ_ONLY_METHODS
        company = (kw.get("context") or {}).get("allowed_company_ids", [None])[0]
        if method == "fields_get":
            return {k: {} for k in ["eh_cash_tolerance_override", "eh_cash_tolerance_amount",
                                    "eh_cash_tolerance_days", "eh_b2b_overdue_days",
                                    "eh_b2b_cash_tolerance_amount", "eh_b2b_cash_tolerance_days"]}
        if model == "res.company" and method == "read":
            return [{"name": "Bakery", "eh_b2b_overdue_days": 30, "eh_b2b_cash_tolerance_amount": 0.0,
                     "eh_b2b_cash_tolerance_days": 0}]
        if model == "account.move.line":
            return LINES[company]
        if model == "res.partner" and method == "search":
            return [p["id"] for p in PARTNERS.values() if p["credit_limit"] > 0]
        if model == "res.partner" and method == "read":
            return [{f: PARTNERS[i].get(f) for f in ["id"] + kw["fields"]} for i in kw["ids"]]
        raise AssertionError((model, method))


class OdooSourceTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.customers, self.invoices, self.other = osrc.load_from_odoo(
            self.client, 124, other_company_id=3, exclude="^ZZ TEST", today=TODAY)

    def test_contacts_roll_up_to_company_and_test_excluded(self):
        self.assertEqual(sorted(self.customers), ["Cafe One", "Cash Shop"])
        self.assertEqual(len(self.invoices["Cafe One"]), 2)
        self.assertEqual(self.customers["Cafe One"]["payment_days"], 15)

    def test_policy_from_company(self):
        self.assertEqual(osrc.company_policy(self.client, 124)["suspend_days"], 30)

    def test_evaluation_with_odoo_data(self):
        policy = cc.Policy(suspend_days=30)
        r = cc.evaluate(self.customers["Cafe One"], self.invoices["Cafe One"], TODAY, policy)
        self.assertEqual(r.status, cc.STATUS_BLOCK)  # INV/1 is 48 days late
        self.assertEqual(r.max_days_overdue, 48)
        cash = cc.evaluate(self.customers["Cash Shop"], self.invoices["Cash Shop"], TODAY, policy)
        self.assertEqual(cash.status, cc.STATUS_CASH)  # 60 within its own 100 tolerance

    def test_other_company_is_info_only(self):
        self.assertEqual(self.other["Cafe One"], (250.0, 155))

    def test_only_read_methods_used(self):
        self.assertTrue(all(m in osrc.READ_ONLY_METHODS for _, m in self.client.calls))
        with self.assertRaises(ValueError):
            osrc.OdooClient(api_key="x").call("res.partner", "write")


if __name__ == "__main__":
    unittest.main()
