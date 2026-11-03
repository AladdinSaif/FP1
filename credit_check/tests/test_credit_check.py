import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import credit_check as cc  # noqa: E402

SAMPLE = HERE.parent / "sample"
TODAY = date(2026, 11, 3)


def run(**kw):
    customers, invoices, _ = cc.load(SAMPLE / "customers.csv", SAMPLE / "invoices.csv")
    return {n: cc.evaluate(customers[n], invoices[n], TODAY, kw.get("grace", 0), 0.8, kw.get("order"))
            for n in customers}


class CreditCheckTest(unittest.TestCase):
    def test_within_limit_not_overdue_is_allowed(self):
        r = run()["مطعم الريحان"]
        self.assertEqual(r.status, cc.STATUS_OK)
        self.assertEqual(r.balance, 4500)
        self.assertEqual(r.available, 5500)

    def test_overdue_invoice_blocks(self):
        r = run()["كافيه لاتيه"]
        self.assertEqual(r.status, cc.STATUS_BLOCK)
        self.assertEqual(r.max_days_overdue, 24)

    def test_grace_days_allow_short_delay(self):
        self.assertEqual(run(grace=30)["كافيه لاتيه"].status, cc.STATUS_OK)

    def test_high_usage_warns(self):
        r = run()["فندق النخيل"]
        self.assertEqual(r.status, cc.STATUS_WARN)
        self.assertEqual(r.usage_pct, 87.5)

    def test_zero_limit_is_cash_only(self):
        self.assertEqual(run()["بقالة الحي"].status, cc.STATUS_CASH)

    def test_over_limit_blocks_and_due_date_from_payment_terms(self):
        # no due date in file -> invoice date + 30 days = 2026-11-14, not overdue yet
        r = run()["مقهى الصباح"]
        self.assertEqual(r.status, cc.STATUS_BLOCK)
        self.assertEqual(r.overdue_amount, 0)
        self.assertIn("تجاوز", r.reasons[0])

    def test_order_bigger_than_available(self):
        customers, invoices, _ = cc.load(SAMPLE / "customers.csv", SAMPLE / "invoices.csv")
        n = "مطعم الريحان"
        self.assertTrue(cc.evaluate(customers[n], invoices[n], TODAY, 0, 0.8, 5000).order_ok)
        self.assertFalse(cc.evaluate(customers[n], invoices[n], TODAY, 0, 0.8, 6000).order_ok)

    def test_payment_terms_parsing(self):
        self.assertEqual(cc.parse_payment_days("30 Days"), 30)
        self.assertEqual(cc.parse_payment_days("Immediate Payment"), 0)
        self.assertEqual(cc.parse_payment_days("٤٥ يوم"), 45)
        self.assertEqual(cc.parse_number("1,250.50"), 1250.5)


if __name__ == "__main__":
    unittest.main()
