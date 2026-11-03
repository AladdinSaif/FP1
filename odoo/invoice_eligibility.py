#!/usr/bin/env python3
"""Who can be invoiced, and who cannot, for one company in an Odoo database.

Read-only: it only calls search_read / read / fields_get. Nothing is written or sent.

Usage:
    ODOO_LOGIN=user@example.com ODOO_API_KEY=... \
    python3 odoo/invoice_eligibility.py --url https://bakery.brioche.sa \
        [--db DBNAME] [--company "الرؤيا الحديثة"] [--out report.xlsx]

The API key is used in place of the password (Odoo: Preferences > Account Security > New API Key).
Pass --login-env / --key-env to read the credentials from other variable names, so that two
instances (for example bakery.brioche.sa and www.foodpassion.sa) can keep separate keys.
"""
import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import urllib.request
from collections import defaultdict

DEFAULT_COMPANY = ["الرؤيا الحديثة", "Modern Advance"]
SA_VAT = re.compile(r"^3\d{13}3$")


class Odoo:
    def __init__(self, url, db, login, key):
        self.url = url.rstrip("/")
        cafile = os.environ.get("SSL_CERT_FILE")
        self.ctx = ssl.create_default_context(cafile=cafile if cafile and os.path.exists(cafile) else None)
        self.db = db or self._guess_db()
        self.key = key
        self.uid = self._call("common", "authenticate", [self.db, login, key, {}])
        if not self.uid:
            sys.exit(f"Login failed on {self.url} (db={self.db}). Check ODOO_LOGIN / ODOO_API_KEY.")
        self.context = {}

    def _post(self, path, params):
        body = json.dumps({"jsonrpc": "2.0", "method": "call", "params": params, "id": 1}).encode()
        req = urllib.request.Request(self.url + path, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=self.ctx, timeout=120) as r:
            res = json.load(r)
        if res.get("error"):
            err = res["error"]
            raise RuntimeError(err.get("data", {}).get("message") or err.get("message"))
        return res["result"]

    def _call(self, service, method, args):
        return self._post("/jsonrpc", {"service": service, "method": method, "args": args})

    def _guess_db(self):
        try:
            dbs = self._post("/web/database/list", {})
        except Exception as e:  # listing is often disabled
            sys.exit(f"Could not list databases on {self.url} ({e}). Pass --db.")
        if len(dbs) != 1:
            sys.exit(f"Databases on {self.url}: {dbs}. Pass --db.")
        return dbs[0]

    def kw(self, model, method, args, **kwargs):
        kwargs.setdefault("context", self.context)
        return self._call("object", "execute_kw", [self.db, self.uid, self.key, model, method, args, kwargs])

    def search_read(self, model, domain, fields, **kw):
        return self.kw(model, "search_read", [domain], fields=fields, **kw)

    def fields(self, model):
        return self.kw(model, "fields_get", [], attributes=["type", "relation", "selection"])

    def has_model(self, model):
        return bool(self.search_read("ir.model", [("model", "=", model)], ["id"]))


def first_existing(available, candidates):
    return next((f for f in candidates if f in available), None)


def find_company(odoo, company_arg):
    if company_arg and company_arg.isdigit():
        rows = odoo.search_read("res.company", [("id", "=", int(company_arg))], ["name"])
    else:
        names = [company_arg] if company_arg else DEFAULT_COMPANY
        domain = ["|"] * (len(names) - 1) + [("name", "ilike", n) for n in names]
        rows = odoo.search_read("res.company", domain, ["name"])
    if not rows:
        all_c = odoo.search_read("res.company", [], ["name"])
        sys.exit(f"Company not found. Companies in this database: {[(c['id'], c['name']) for c in all_c]}")
    if len(rows) > 1:
        sys.exit(f"Several companies match: {[(c['id'], c['name']) for c in rows]}. Pass --company <id>.")
    return rows[0]


def linked_records(odoo, model, partner_ids):
    """State of a custom per-customer record (contract, onboarding) if that model exists."""
    if not odoo.has_model(model):
        return None
    flds = odoo.fields(model)
    pfield = next((f for f in ("partner_id", "customer_id", "commercial_partner_id")
                   if flds.get(f, {}).get("relation") == "res.partner"), None)
    if not pfield or "state" not in flds:
        return None
    labels = dict(flds["state"].get("selection") or [])
    out = defaultdict(list)
    for r in odoo.search_read(model, [(pfield, "in", partner_ids)], [pfield, "state", "display_name"]):
        out[r[pfield][0]].append(f"{r['display_name']}: {labels.get(r['state'], r['state'])}")
    return out


def collect(odoo, company):
    cid = company["id"]
    odoo.context = {"allowed_company_ids": [cid], "lang": "ar_001", "active_test": True}
    pf = odoo.fields("res.partner")
    cf = odoo.fields("res.company")

    addr = {
        "الشارع": "street",
        "الحي": "street2",
        "المدينة": "city",
        "الرمز البريدي": "zip",
        "رقم المبنى": first_existing(pf, ["l10n_sa_edi_building_number", "building_number", "street_number"]),
        "الرقم الإضافي": first_existing(pf, ["l10n_sa_edi_plot_identification", "l10n_sa_edi_additional_number"]),
    }
    addr = {k: v for k, v in addr.items() if v}
    id_scheme = first_existing(pf, ["l10n_sa_additional_identification_scheme"])
    id_number = first_existing(pf, ["l10n_sa_additional_identification_number"])
    optional = [f for f in ("credit", "credit_limit", "use_partner_credit_limit", "invoice_warn", "invoice_warn_msg",
                            "customer_rank", "company_id", "is_company", "property_payment_term_id")
                if f in pf]
    fields = ["name", "display_name", "vat", "email", "phone"] + list(addr.values()) + optional
    fields += [f for f in (id_scheme, id_number) if f]

    company_cfg = odoo.search_read("res.company", [("id", "=", cid)],
                                   [f for f in ("account_use_credit_limit", "account_default_credit_limit") if f in cf])[0]

    # Customers: commercial partners with a customer rank or any sale order in this company.
    so_partner_ids = set()
    to_invoice = defaultdict(float)
    for so in odoo.search_read("sale.order", [("company_id", "=", cid), ("state", "=", "sale")],
                               ["partner_id", "invoice_status", "amount_to_invoice", "name"]):
        so_partner_ids.add(so["partner_id"][0])
        if so["invoice_status"] == "to invoice":
            to_invoice[so["partner_id"][0]] += so.get("amount_to_invoice") or 0.0

    partner_domain = [("parent_id", "=", False), ("company_id", "in", [False, cid])]
    partners = odoo.search_read("res.partner", partner_domain + [("customer_rank", ">", 0)], fields)
    known = {p["id"] for p in partners}
    extra = so_partner_ids - known
    if extra:
        rows = odoo.search_read("res.partner", [("id", "in", list(extra))], fields + ["commercial_partner_id"])
        for r in rows:  # roll child contacts up to their company
            cp = r["commercial_partner_id"][0]
            if cp != r["id"]:
                to_invoice[cp] += to_invoice.pop(r["id"], 0.0)
        missing = {r["commercial_partner_id"][0] for r in rows} - known
        if missing:
            partners += odoo.search_read("res.partner", [("id", "in", list(missing))], fields)

    today = dt.date.today()
    overdue = defaultdict(float)
    open_inv = defaultdict(list)
    for inv in odoo.search_read("account.move",
                                [("company_id", "=", cid), ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                                 ("payment_state", "in", ["not_paid", "partial"])],
                                ["commercial_partner_id", "amount_residual", "name", "invoice_date",
                                 "invoice_date_due"]):
        pid = inv["commercial_partner_id"][0]
        open_inv[pid].append(inv)
        if inv["invoice_date_due"] and inv["invoice_date_due"] < today.isoformat():
            overdue[pid] += inv["amount_residual"]

    ids = [p["id"] for p in partners]
    contracts = linked_records(odoo, "eh.b2b.contract", ids)
    onboarding = linked_records(odoo, "eh.b2b.onboarding", ids)
    return dict(partners=partners, addr=addr, id_number=id_number, company_cfg=company_cfg,
                to_invoice=to_invoice, overdue=overdue, contracts=contracts, onboarding=onboarding,
                open_inv=open_inv, terms=payment_term_days(odoo, partners), today=today)


def payment_term_days(odoo, partners):
    """{term_id: (name, days)} where days is the longest line of the term (0 = immediate)."""
    term_ids = list({p["property_payment_term_id"][0] for p in partners if p.get("property_payment_term_id")})
    if not term_ids:
        return {}
    lf = odoo.fields("account.payment.term.line")
    day_field = first_existing(lf, ["nb_days", "days"])
    terms = odoo.search_read("account.payment.term", [("id", "in", term_ids)], ["name", "line_ids"])
    lines = {l["id"]: l for l in odoo.search_read("account.payment.term.line",
                                                  [("payment_id", "in", term_ids)], ["id", day_field])}
    return {t["id"]: (t["name"], max([lines[i][day_field] or 0 for i in t["line_ids"] if i in lines] or [0]))
            for t in terms}


def build_credit_rows(data):
    """Credit limit usage and payment-term (days) status per customer."""
    rows = []
    today = data["today"]
    use_limit = data["company_cfg"].get("account_use_credit_limit", True)
    for p in data["partners"]:
        limit = (p.get("credit_limit") or 0.0) if use_limit else 0.0
        credit = p.get("credit") or 0.0
        invs = data["open_inv"].get(p["id"], [])
        term = p.get("property_payment_term_id")
        term_name, term_days = data["terms"].get(term[0], (term[1], None)) if term else ("بدون شروط (فوري)", 0)
        if not limit and not invs:
            continue
        dated = [i for i in invs if i["invoice_date_due"]]
        late = [i for i in dated if i["invoice_date_due"] < today.isoformat()]
        due_today = [i for i in dated if i["invoice_date_due"] == today.isoformat()]
        oldest = min(dated, key=lambda i: i["invoice_date_due"]) if dated else None
        max_late = (today - dt.date.fromisoformat(oldest["invoice_date_due"])).days if oldest else 0
        oldest_age = (today - dt.date.fromisoformat(oldest["invoice_date"])).days \
            if oldest and oldest["invoice_date"] else None

        if not limit:
            limit_status = "ما في حد"
        elif credit >= limit:
            limit_status = "واصل الحد"
        elif credit >= 0.8 * limit:
            limit_status = "قرّب (80%+)"
        else:
            limit_status = "ضمن الحد"
        if late:
            term_status = "واصل حد أيام السداد"
        elif due_today:
            term_status = "تستحق اليوم"
        else:
            term_status = "ضمن المدة" if invs else "ما في فواتير مفتوحة"

        rows.append({
            "العميل": p["display_name"],
            "ID": p["id"],
            "الحد الائتماني": round(limit, 2),
            "الرصيد المستحق": round(credit, 2),
            "نسبة الاستخدام %": round(100 * credit / limit, 1) if limit else "",
            "المتبقي من الحد": round(limit - credit, 2) if limit else "",
            "حالة الحد": limit_status,
            "شروط السداد": term_name,
            "أيام السداد": term_days if term_days is not None else "",
            "عمر أقدم فاتورة مفتوحة (يوم)": oldest_age if oldest_age is not None else "",
            "أكثر تأخير (يوم)": max(max_late, 0),
            "فواتير متأخرة": len(late),
            "مبلغ متأخر": round(sum(i["amount_residual"] for i in late), 2),
            "أقدم فاتورة متأخرة": min(late, key=lambda i: i["invoice_date_due"])["name"] if late else "",
            "حالة أيام السداد": term_status,
        })
    rank_l = {"واصل الحد": 0, "قرّب (80%+)": 1, "ضمن الحد": 2, "ما في حد": 3}
    rank_t = {"واصل حد أيام السداد": 0, "تستحق اليوم": 1, "ضمن المدة": 2, "ما في فواتير مفتوحة": 3}
    rows.sort(key=lambda r: (rank_l[r["حالة الحد"]], rank_t[r["حالة أيام السداد"]], -r["أكثر تأخير (يوم)"]))
    return rows


def classify(p, data):
    """Return (verdict, reasons_block, reasons_fix, notes)."""
    block, fix, notes = [], [], []
    cfg = data["company_cfg"]

    if p.get("invoice_warn") == "block":
        block.append("عليه حظر فوترة: " + (p.get("invoice_warn_msg") or "").strip())
    elif p.get("invoice_warn_msg") and p.get("invoice_warn") not in (None, False, "no-message"):
        notes.append("تحذير على الفاتورة: " + p["invoice_warn_msg"].strip())

    credit = p.get("credit") or 0.0
    limit = p.get("credit_limit") or 0.0
    pending = data["to_invoice"].get(p["id"], 0.0)
    if cfg.get("account_use_credit_limit", True) and limit > 0:
        if credit >= limit:
            block.append(f"تجاوز الحد الائتماني: الرصيد {credit:,.2f} من حد {limit:,.2f}")
        elif credit + pending > limit:
            notes.append(f"الفوترة رح توصل الرصيد {credit + pending:,.2f} وتتجاوز الحد {limit:,.2f}")

    vat = (p.get("vat") or "").replace(" ", "")
    if not vat:
        fix.append("ما في رقم ضريبي (فاتورة مبسطة فقط، مش فاتورة ضريبية B2B)")
    elif not SA_VAT.match(vat):
        fix.append(f"الرقم الضريبي غير صحيح: {vat}")

    missing = [label for label, f in data["addr"].items() if not (p.get(f) or "").strip()]
    if missing:
        fix.append("العنوان الوطني ناقص: " + "، ".join(missing))
    if data["id_number"] and not p.get(data["id_number"]):
        notes.append("ما في سجل تجاري أو رقم هوية إضافي")

    if data["overdue"].get(p["id"]):
        notes.append(f"فواتير متأخرة السداد: {data['overdue'][p['id']]:,.2f}")
    if data["contracts"] is not None:
        notes.append("العقد: " + ("؛ ".join(data["contracts"].get(p["id"], [])) or "ما في عقد"))
    if data["onboarding"] is not None and data["onboarding"].get(p["id"]):
        notes.append("التسجيل: " + "؛ ".join(data["onboarding"][p["id"]]))

    if block:
        verdict = "لا تصدر فاتورة"
    elif fix:
        verdict = "استكمل البيانات أولاً"
    else:
        verdict = "تصدر فاتورة"
    return verdict, block, fix, notes


ORDER = {"لا تصدر فاتورة": 0, "استكمل البيانات أولاً": 1, "تصدر فاتورة": 2}


def build_rows(data):
    rows = []
    for p in data["partners"]:
        verdict, block, fix, notes = classify(p, data)
        rows.append({
            "الحكم": verdict,
            "العميل": p["display_name"],
            "ID": p["id"],
            "الرقم الضريبي": p.get("vat") or "",
            "الرصيد المستحق": round(p.get("credit") or 0.0, 2),
            "الحد الائتماني": round(p.get("credit_limit") or 0.0, 2),
            "جاهز للفوترة (طلبات)": round(data["to_invoice"].get(p["id"], 0.0), 2),
            "متأخر السداد": round(data["overdue"].get(p["id"], 0.0), 2),
            "سبب المنع": " | ".join(block),
            "بيانات ناقصة": " | ".join(fix),
            "ملاحظات": " | ".join(notes),
        })
    rows.sort(key=lambda r: (ORDER[r["الحكم"]], -r["جاهز للفوترة (طلبات)"], r["العميل"]))
    return rows


def write_xlsx(rows, path, title):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoicing"
    ws.sheet_view.rightToLeft = True
    headers = list(rows[0].keys()) if rows else ["الحكم"]
    ws.append([title])
    ws.append(headers)
    for c in ws[2]:
        c.font = Font(bold=True)
    red, amber, green = "F8D7DA", "FFF3CD", "D4EDDA"
    fills = {"لا تصدر فاتورة": red, "استكمل البيانات أولاً": amber, "تصدر فاتورة": green,
             "واصل الحد": red, "قرّب (80%+)": amber, "ضمن الحد": green,
             "واصل حد أيام السداد": red, "تستحق اليوم": amber, "ضمن المدة": green}
    for r in rows:
        ws.append(list(r.values()))
        for i, v in enumerate(r.values(), 1):
            if isinstance(v, str) and v in fills:
                ws.cell(ws.max_row, i).fill = PatternFill("solid", fgColor=fills[v])
    from openpyxl.utils import get_column_letter
    for i, h in enumerate(headers, 1):
        width = max([len(str(h))] + [len(str(r[h])) for r in rows]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 60)
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--db")
    ap.add_argument("--company", help="company id or part of its name (default: الرؤيا الحديثة / Modern Advance)")
    ap.add_argument("--login-env", default="ODOO_LOGIN")
    ap.add_argument("--key-env", default="ODOO_API_KEY")
    ap.add_argument("--out", help="write an .xlsx report here")
    ap.add_argument("--json", action="store_true", help="print rows as JSON instead of a summary")
    ap.add_argument("--credit", action="store_true",
                    help="credit-limit and payment-days report instead of the invoicing verdict")
    a = ap.parse_args()

    login, key = os.environ.get(a.login_env), os.environ.get(a.key_env)
    if not login or not key:
        sys.exit(f"Set {a.login_env} and {a.key_env} (an Odoo API key, never a password typed into chat).")

    odoo = Odoo(a.url, a.db, login, key)
    company = find_company(odoo, a.company)
    data = collect(odoo, company)
    rows = build_credit_rows(data) if a.credit else build_rows(data)
    title = f"{company['name']} - {a.url} - {dt.date.today().isoformat()}"

    if a.out:
        write_xlsx(rows, a.out, title)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(title)
    if a.credit:
        for r in rows:
            lim = f"حد {r['الحد الائتماني']:,.2f} / رصيد {r['الرصيد المستحق']:,.2f} ({r['حالة الحد']})" \
                if r["الحد الائتماني"] else f"بدون حد / رصيد {r['الرصيد المستحق']:,.2f}"
            late = f"، متأخر {r['أكثر تأخير (يوم)']} يوم بمبلغ {r['مبلغ متأخر']:,.2f}" if r["فواتير متأخرة"] else ""
            print(f"- {r['العميل']}: {lim} | {r['شروط السداد']}: {r['حالة أيام السداد']}{late}")
        if a.out:
            print(f"\nReport: {a.out}")
        return
    counts = defaultdict(int)
    for r in rows:
        counts[r["الحكم"]] += 1
    print("  ".join(f"{k}: {counts[k]}" for k in ORDER))
    for r in rows:
        why = r["سبب المنع"] or r["بيانات ناقصة"]
        print(f"- [{r['الحكم']}] {r['العميل']}" + (f" -> {why}" if why else ""))
    if a.out:
        print(f"\nReport: {a.out}")


if __name__ == "__main__":
    main()
