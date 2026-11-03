#!/usr/bin/env python3
"""فحص ائتمان عملاء المخبز: مين نقدر نبيع له آجل ومين لا.

يقرأ ملفين (CSV أو Excel)، غالباً مُصدّرين من Odoo:
  1) العملاء: الاسم، الحد الائتماني، أيام السداد (شرط الدفع)
  2) الفواتير المفتوحة: العميل، تاريخ الفاتورة، تاريخ الاستحقاق، المبلغ المتبقي

أو يسحب نفس البيانات مباشرة من أودو (--odoo، انظر odoo_source.py).

القواعد (قابلة للتعديل من سطر الأوامر، وبوضع أودو تنقرأ من إعدادات الشركة):
  - "متأخر" = فاتورة عدّى تاريخ استحقاقها بأكثر من مهلة السماح (--grace-days).
    الدفعات والإشعارات الدائنة غير المسوّاة تنخصم من أقدم الفواتير المتأخرة.
  - عميل آجل (حد > 0):
      ممنوع: متأخر أكثر من --suspend-days يوم، أو رصيده وصل الحد أو تجاوزه.
      انتبه: عليه متأخر (ضمن --suspend-days)، أو استهلك 80% أو أكثر من حده.
      مسموح: غير ذلك، والمتاح = الحد - الرصيد.
  - عميل كاش (حد = 0):
      ممنوع: عليه متأخر أكثر من مبلغ التسامح وأقدمه أكثر من أيام التسامح.
      كاش فقط: غير ذلك (البيع بالدفع عند الاستلام).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

# أسماء الأعمدة المقبولة (إنجليزي / Odoo / عربي). المقارنة بدون حالة أحرف ومسافات.
CUSTOMER_COLS = {
    "name": ["name", "display name", "customer", "partner", "العميل", "اسم العميل", "الاسم"],
    "credit_limit": ["credit_limit", "credit limit", "الحد الائتماني", "حد الائتمان", "الحد"],
    "payment_days": [
        "payment_days", "payment days", "payment terms", "customer payment terms",
        "property_payment_term_id", "أيام السداد", "شروط الدفع", "مدة السداد",
    ],
}
INVOICE_COLS = {
    "customer": ["customer", "partner", "partner_id", "name", "العميل", "اسم العميل"],
    "invoice_date": ["invoice_date", "invoice date", "invoice/bill date", "date", "تاريخ الفاتورة"],
    "due_date": ["invoice_date_due", "due date", "due_date", "تاريخ الاستحقاق"],
    "amount_due": [
        "amount_residual", "amount due", "amount_due", "residual", "balance",
        "المبلغ المتبقي", "المتبقي", "المستحق",
    ],
    "number": ["number", "invoice", "reference", "رقم الفاتورة"],
}

STATUS_BLOCK = "ممنوع"
STATUS_CASH = "كاش فقط"
STATUS_WARN = "انتبه"
STATUS_OK = "مسموح"
STATUS_ORDER = {STATUS_BLOCK: 0, STATUS_WARN: 1, STATUS_CASH: 2, STATUS_OK: 3}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def read_table(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            sys.exit("لقراءة ملفات Excel ثبّت openpyxl:  pip install openpyxl  (أو احفظ الملف CSV)")
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(h or "").strip() for h in rows[0]]
        return [
            {header[i]: ("" if v is None else v) for i, v in enumerate(r) if i < len(header)}
            for r in rows[1:]
            if any(v not in (None, "") for v in r)
        ]
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]


def map_columns(rows, wanted: dict[str, list[str]], required: list[str], label: str) -> dict[str, str]:
    headers = list(rows[0].keys()) if rows else []
    by_norm = {_norm(h): h for h in headers}
    mapping = {}
    for key, aliases in wanted.items():
        for a in aliases:
            if _norm(a) in by_norm:
                mapping[key] = by_norm[_norm(a)]
                break
    missing = [k for k in required if k not in mapping]
    if missing:
        sys.exit(
            f"ملف {label}: ما لقيت الأعمدة {missing}.\n"
            f"الأعمدة الموجودة: {headers}\n"
            f"الأسماء المقبولة: { {k: wanted[k] for k in missing} }"
        )
    return mapping


def parse_number(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("SAR", "").replace("ر.س", "").strip()
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = float(s)
    except ValueError:
        return 0.0
    return -n if neg else n


def parse_payment_days(v) -> int:
    """'30 Days' -> 30, 'Immediate Payment' / 'فوري' / فاضي -> 0, 'End of Following Month' -> 45 تقريباً."""
    if isinstance(v, (int, float)):
        return int(v)
    s = _norm(v).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not s or "immediate" in s or "فوري" in s or "كاش" in s or "cash" in s:
        return 0
    if "following month" in s or "الشهر التالي" in s:
        return 45
    m = re.search(r"\d+", s)
    return int(m.group()) if m else 0


def parse_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Invoice:
    number: str
    invoice_date: date | None
    due_date: date | None
    amount_due: float


@dataclass
class Policy:
    grace_days: int = 0          # تأخير ضمن هالمهلة ما بينحسب متأخر أصلاً
    suspend_days: int = 0        # عميل آجل متأخر أكثر من هيك = ممنوع
    warn_usage: float = 0.8      # نسبة استهلاك الحد للتنبيه
    cash_tol_amount: float = 0.0  # عميل كاش: مبلغ متأخر مسموح
    cash_tol_days: int = 0        # عميل كاش: أيام تأخير مسموحة
    min_amount: float = 1.0       # مبالغ أقل من هيك (فروقات تقريب) بتنتجاهل


@dataclass
class Result:
    name: str
    credit_limit: float
    payment_days: int
    balance: float = 0.0
    overdue_amount: float = 0.0
    max_days_overdue: int = 0
    oldest_overdue: str = ""
    available: float = 0.0
    usage_pct: float | None = None
    status: str = STATUS_OK
    reasons: list[str] = field(default_factory=list)
    order_amount: float | None = None
    order_ok: bool | None = None
    other_balance: float | None = None
    other_max_days: int = 0


def overdue_summary(invoices: list[Invoice], payment_days: int, today: date, policy: Policy):
    """يرجع (الرصيد، المبلغ المتأخر، أقصى أيام تأخير، رقم أقدم فاتورة متأخرة)."""
    balance = sum(i.amount_due for i in invoices)
    unapplied = -sum(i.amount_due for i in invoices if i.amount_due < 0)
    late = []
    for inv in invoices:
        if inv.amount_due < policy.min_amount:
            continue
        due = inv.due_date
        if due is None and inv.invoice_date is not None:
            due = inv.invoice_date + timedelta(days=payment_days)
        if due is None:
            continue
        days = (today - due).days
        if days > policy.grace_days:
            late.append((days, inv))
    late.sort(key=lambda t: -t[0])  # الأقدم أولاً: الدفعات غير المسوّاة بتسكّره قبل غيره
    amount, max_days, oldest = 0.0, 0, ""
    for days, inv in late:
        used = min(unapplied, inv.amount_due)
        unapplied -= used
        left = inv.amount_due - used
        if left >= policy.min_amount:
            amount += left
            if days > max_days:
                max_days, oldest = days, inv.number
    return round(balance, 2), round(amount, 2), max_days, oldest


def evaluate(customer: dict, invoices: list[Invoice], today: date, policy: Policy | None = None,
             order_amount: float | None = None) -> Result:
    policy = policy or Policy()
    r = Result(customer["name"], customer["credit_limit"], customer["payment_days"])
    r.balance, r.overdue_amount, r.max_days_overdue, r.oldest_overdue = overdue_summary(
        invoices, r.payment_days, today, policy)
    r.available = round(max(r.credit_limit - r.balance, 0.0), 2)
    if r.credit_limit > 0:
        r.usage_pct = round(r.balance / r.credit_limit * 100, 1)
    late_text = (
        f"متأخر {r.max_days_overdue} يوم عن الاستحقاق، مبلغ متأخر {r.overdue_amount:,.2f}"
        + (f" (أقدم فاتورة {r.oldest_overdue})" if r.oldest_overdue else "")
    )

    if r.credit_limit > 0:
        if r.overdue_amount > 0 and r.max_days_overdue > policy.suspend_days:
            r.reasons.append(late_text)
        if r.balance >= r.credit_limit:
            r.reasons.append(f"الرصيد {r.balance:,.2f} وصل/تجاوز الحد {r.credit_limit:,.2f}")
        if r.reasons:
            r.status = STATUS_BLOCK
        elif r.overdue_amount > 0:
            r.status = STATUS_WARN
            r.reasons.append(late_text + f"، المتاح {r.available:,.2f}")
        elif r.usage_pct >= policy.warn_usage * 100:
            r.status = STATUS_WARN
            r.reasons.append(f"مستهلك {r.usage_pct}% من حده، المتاح {r.available:,.2f}")
        else:
            r.reasons.append(f"المتاح {r.available:,.2f}")
    else:
        tol_amount = customer.get("cash_tol_amount", policy.cash_tol_amount)
        tol_days = customer.get("cash_tol_days", policy.cash_tol_days)
        if r.overdue_amount > tol_amount and r.max_days_overdue > tol_days:
            r.status = STATUS_BLOCK
            r.reasons.append("عميل كاش وعليه فواتير غير مدفوعة: " + late_text)
        else:
            r.status = STATUS_CASH
            r.reasons.append("ما له حد ائتماني، البيع نقداً فقط")

    if order_amount is not None:
        r.order_amount = order_amount
        r.order_ok = r.status in (STATUS_OK, STATUS_WARN) and order_amount <= r.available
        if not r.order_ok and r.status in (STATUS_OK, STATUS_WARN):
            r.reasons.append(f"الطلب {order_amount:,.2f} أكبر من المتاح {r.available:,.2f}")
    return r


def load(customers_path: Path, invoices_path: Path | None):
    crow = read_table(customers_path)
    cm = map_columns(crow, CUSTOMER_COLS, ["name", "credit_limit"], "العملاء")
    customers = {}
    for row in crow:
        name = str(row.get(cm["name"], "")).strip()
        if not name:
            continue
        customers[name] = {
            "name": name,
            "credit_limit": parse_number(row.get(cm["credit_limit"])),
            "payment_days": parse_payment_days(row.get(cm["payment_days"])) if "payment_days" in cm else 0,
        }
    invoices: dict[str, list[Invoice]] = {n: [] for n in customers}
    unknown = set()
    if invoices_path:
        irow = read_table(invoices_path)
        im = map_columns(irow, INVOICE_COLS, ["customer", "amount_due"], "الفواتير")
        if "due_date" not in im and "invoice_date" not in im:
            sys.exit("ملف الفواتير لازم يكون فيه تاريخ الاستحقاق أو تاريخ الفاتورة.")
        for i, row in enumerate(irow, start=2):
            cust = str(row.get(im["customer"], "")).strip()
            if not cust:
                continue
            inv = Invoice(
                number=str(row.get(im["number"], "")).strip() if "number" in im else f"سطر {i}",
                invoice_date=parse_date(row.get(im["invoice_date"])) if "invoice_date" in im else None,
                due_date=parse_date(row.get(im["due_date"])) if "due_date" in im else None,
                amount_due=parse_number(row.get(im["amount_due"])),
            )
            if cust not in customers:
                unknown.add(cust)
                customers[cust] = {"name": cust, "credit_limit": 0.0, "payment_days": 0}
                invoices[cust] = []
            invoices[cust].append(inv)
    return customers, invoices, unknown


def write_report(results: list[Result], path: Path):
    headers = ["الحالة", "العميل", "الحد الائتماني", "أيام السداد", "الرصيد المفتوح", "المتاح",
               "نسبة الاستهلاك %", "المبلغ المتأخر", "أقصى تأخير (يوم)", "السبب"]
    rows = [[r.status, r.name, r.credit_limit, r.payment_days, r.balance, r.available,
             "" if r.usage_pct is None else r.usage_pct, r.overdue_amount, r.max_days_overdue,
             " | ".join(r.reasons)] for r in results]
    if any(r.other_balance is not None for r in results):
        headers += ["رصيد الشركة الثانية (للعلم)", "تأخيره (يوم)"]
        for row, r in zip(rows, results):
            row += [round(r.other_balance or 0.0, 2), r.other_max_days]
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "فحص الائتمان"
        ws.sheet_view.rightToLeft = True
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
        fills = {STATUS_BLOCK: "F8CBAD", STATUS_WARN: "FFE699", STATUS_CASH: "D9D9D9", STATUS_OK: "C6EFCE"}
        for row in rows:
            ws.append(row)
            ws.cell(ws.max_row, 1).fill = PatternFill("solid", fgColor=fills[row[0]])
        for col, w in zip("ABCDEFGHIJKL", (10, 40, 14, 11, 14, 14, 14, 14, 14, 70, 16, 12)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        wb.save(path)
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description="فحص ائتمان عملاء المخبز")
    src = p.add_argument_group("مصدر البيانات (ملفات أو أودو)")
    src.add_argument("--customers", type=Path, help="ملف العملاء (csv/xlsx)")
    src.add_argument("--invoices", type=Path, help="ملف الفواتير المفتوحة (csv/xlsx)")
    src.add_argument("--odoo", action="store_true",
                     help="اسحب مباشرة من أودو (يحتاج ODOO_API_KEY، واختيارياً ODOO_URL و ODOO_DB)")
    src.add_argument("--company", type=int, help="أودو: رقم الشركة (افتراضي: شركة المستخدم الحالية)")
    src.add_argument("--other-company", type=int,
                     help="أودو: شركة ثانية يظهر رصيدها للعلم بس، ما بيأثر عالقرار (مثلاً الشركة القديمة 3)")
    src.add_argument("--exclude", help="أودو: تجاهل العملاء اللي اسمهم يطابق هالنمط (regex)، مثلاً '^ZZ TEST'")
    p.add_argument("--today", help="تاريخ الفحص YYYY-MM-DD (افتراضي: اليوم)")
    p.add_argument("--grace-days", type=int, default=0, help="مهلة سماح بعد الاستحقاق بالأيام (افتراضي 0)")
    p.add_argument("--suspend-days", type=int,
                   help="عميل آجل متأخر أكثر من هيك يوم = ممنوع (افتراضي: 0، وبأودو من إعداد الشركة)")
    p.add_argument("--warn-usage", type=float, default=0.8, help="نسبة استهلاك الحد للتنبيه (افتراضي 0.8)")
    p.add_argument("--customer", help="افحص عميل واحد بالاسم (جزء من الاسم يكفي)")
    p.add_argument("--order", type=float, help="قيمة طلب جديد للتأكد إذا يمشي (مع --customer)")
    p.add_argument("--out", type=Path, help="احفظ التقرير (csv أو xlsx)")
    a = p.parse_args(argv)
    if bool(a.odoo) == bool(a.customers):
        p.error("حدد مصدر واحد: --customers (مع --invoices) أو --odoo")

    today = parse_date(a.today) if a.today else date.today()
    policy = Policy(grace_days=a.grace_days, suspend_days=a.suspend_days or 0, warn_usage=a.warn_usage)
    other: dict[str, tuple[float, int]] = {}
    unknown: set[str] = set()
    if a.odoo:
        from odoo_source import OdooClient, company_policy, default_company, load_from_odoo
        client = OdooClient()
        company = a.company or default_company(client)
        cp = company_policy(client, company)
        if a.suspend_days is None and cp["suspend_days"] is not None:
            policy.suspend_days = int(cp["suspend_days"])
        if cp["cash_tol_amount"] is not None:
            policy.cash_tol_amount = float(cp["cash_tol_amount"])
        if cp["cash_tol_days"] is not None:
            policy.cash_tol_days = int(cp["cash_tol_days"])
        print(f"أودو: {client.url}، الشركة {cp['name']} ({company})")
        customers, invoices, other = load_from_odoo(client, company, a.other_company, a.exclude, today)
    else:
        customers, invoices, unknown = load(a.customers, a.invoices)

    names = list(customers)
    if a.customer:
        names = [n for n in names if _norm(a.customer) in _norm(n)]
        if not names:
            sys.exit(f"ما لقيت عميل اسمه يحتوي: {a.customer}")
    results = []
    for n in names:
        r = evaluate(customers[n], invoices.get(n, []), today, policy, a.order if a.customer else None)
        if n in other:
            r.other_balance, r.other_max_days = round(other[n][0], 2), other[n][1]
        elif other:
            r.other_balance = 0.0
        results.append(r)
    results.sort(key=lambda r: (STATUS_ORDER[r.status], -r.max_days_overdue, -r.balance))

    print(f"فحص الائتمان بتاريخ {today.isoformat()}  (مهلة السماح {policy.grace_days} يوم، "
          f"إيقاف الآجل بعد {policy.suspend_days} يوم، تسامح الكاش {policy.cash_tol_amount:,.0f} / "
          f"{policy.cash_tol_days} يوم)\n")
    for r in results:
        line = f"[{r.status}] {r.name}: " + " | ".join(r.reasons)
        if r.other_balance and abs(r.other_balance) >= 1:
            line += f" | رصيد الشركة الثانية {r.other_balance:,.2f}"
        if r.order_amount is not None:
            line += f"\n    ← الطلب {r.order_amount:,.2f}: " + ("يمشي ✅" if r.order_ok else "لا يمشي ❌")
        print(line)
    counts = {s: sum(r.status == s for r in results) for s in STATUS_ORDER}
    print("\nالملخص: " + "، ".join(f"{s} {c}" for s, c in counts.items()))
    if unknown:
        print(f"\nتنبيه: فواتير لعملاء مو موجودين بملف العملاء (اعتبرناهم بدون حد): {sorted(unknown)}")
    if a.out:
        write_report(results, a.out)
        print(f"\nالتقرير محفوظ: {a.out}")
    return results


if __name__ == "__main__":
    main()
