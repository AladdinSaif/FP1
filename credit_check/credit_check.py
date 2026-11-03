#!/usr/bin/env python3
"""فحص ائتمان عملاء المخبز: مين نقدر نبيع له آجل ومين لا.

يقرأ ملفين (CSV أو Excel)، غالباً مُصدّرين من Odoo:
  1) العملاء: الاسم، الحد الائتماني، أيام السداد (شرط الدفع)
  2) الفواتير المفتوحة: العميل، تاريخ الفاتورة، تاريخ الاستحقاق، المبلغ المتبقي

القواعد (قابلة للتعديل من سطر الأوامر):
  - ممنوع: عنده فاتورة متأخرة عن تاريخ استحقاقها أكثر من مهلة السماح،
           أو رصيده المفتوح وصل الحد الائتماني أو تجاوزه.
  - كاش فقط: حده الائتماني صفر (أو فاضي) وما عليه شي متأخر.
  - انتبه: مسموح بس استهلك نسبة عالية من حده (افتراضياً 80%).
  - مسموح: غير ذلك، ويطلع المتاح له = الحد - الرصيد.
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


def evaluate(customer: dict, invoices: list[Invoice], today: date, grace_days: int, warn_usage: float,
             order_amount: float | None = None) -> Result:
    r = Result(customer["name"], customer["credit_limit"], customer["payment_days"])
    for inv in invoices:
        if inv.amount_due <= 0.005:
            continue
        r.balance += inv.amount_due
        due = inv.due_date
        if due is None and inv.invoice_date is not None:
            due = inv.invoice_date + timedelta(days=r.payment_days)
        if due is None:
            continue
        late = (today - due).days
        if late > grace_days:
            r.overdue_amount += inv.amount_due
            if late > r.max_days_overdue:
                r.max_days_overdue = late
                r.oldest_overdue = inv.number
    r.balance = round(r.balance, 2)
    r.overdue_amount = round(r.overdue_amount, 2)
    r.available = round(max(r.credit_limit - r.balance, 0.0), 2)
    if r.credit_limit > 0:
        r.usage_pct = round(r.balance / r.credit_limit * 100, 1)

    if r.overdue_amount > 0:
        r.reasons.append(
            f"متأخر {r.max_days_overdue} يوم عن الاستحقاق، مبلغ متأخر {r.overdue_amount:,.2f}"
            + (f" (أقدم فاتورة {r.oldest_overdue})" if r.oldest_overdue else "")
        )
    if r.credit_limit > 0 and r.balance >= r.credit_limit:
        r.reasons.append(f"الرصيد {r.balance:,.2f} وصل/تجاوز الحد {r.credit_limit:,.2f}")

    if r.reasons:
        r.status = STATUS_BLOCK
    elif r.credit_limit <= 0:
        r.status = STATUS_CASH
        r.reasons.append("ما له حد ائتماني، البيع نقداً فقط")
    elif r.usage_pct is not None and r.usage_pct >= warn_usage * 100:
        r.status = STATUS_WARN
        r.reasons.append(f"مستهلك {r.usage_pct}% من حده، المتاح {r.available:,.2f}")
    else:
        r.reasons.append(f"المتاح {r.available:,.2f}")

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
        for col, w in zip("ABCDEFGHIJ", (10, 30, 14, 11, 14, 14, 14, 14, 14, 70)):
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
    p.add_argument("--customers", required=True, type=Path, help="ملف العملاء (csv/xlsx)")
    p.add_argument("--invoices", type=Path, help="ملف الفواتير المفتوحة (csv/xlsx)")
    p.add_argument("--today", help="تاريخ الفحص YYYY-MM-DD (افتراضي: اليوم)")
    p.add_argument("--grace-days", type=int, default=0, help="مهلة سماح بعد الاستحقاق بالأيام (افتراضي 0)")
    p.add_argument("--warn-usage", type=float, default=0.8, help="نسبة استهلاك الحد للتنبيه (افتراضي 0.8)")
    p.add_argument("--customer", help="افحص عميل واحد بالاسم (جزء من الاسم يكفي)")
    p.add_argument("--order", type=float, help="قيمة طلب جديد للتأكد إذا يمشي (مع --customer)")
    p.add_argument("--out", type=Path, help="احفظ التقرير (csv أو xlsx)")
    a = p.parse_args(argv)

    today = parse_date(a.today) if a.today else date.today()
    customers, invoices, unknown = load(a.customers, a.invoices)

    names = list(customers)
    if a.customer:
        names = [n for n in names if _norm(a.customer) in _norm(n)]
        if not names:
            sys.exit(f"ما لقيت عميل اسمه يحتوي: {a.customer}")
    results = [evaluate(customers[n], invoices.get(n, []), today, a.grace_days, a.warn_usage,
                        a.order if a.customer else None) for n in names]
    results.sort(key=lambda r: (STATUS_ORDER[r.status], -r.max_days_overdue, -r.balance))

    print(f"فحص الائتمان بتاريخ {today.isoformat()}  (مهلة السماح {a.grace_days} يوم)\n")
    for r in results:
        line = f"[{r.status}] {r.name}: " + " | ".join(r.reasons)
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
