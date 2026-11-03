"""سحب بيانات فحص الائتمان مباشرة من أودو (قراءة فقط).

يستخدم واجهة JSON-2 تبع أودو 19:  POST {url}/json/2/<model>/<method>
مع ترويسة  Authorization: bearer <API key>.

الإعدادات من متغيرات البيئة:
  ODOO_API_KEY  (إجباري)  مفتاح API لمستخدم أودو. لا تحطه بالكود ولا بالمحادثة.
  ODOO_URL      (اختياري) افتراضياً https://bakery.brioche.sa
  ODOO_DB       (اختياري) اسم قاعدة البيانات إذا السيرفر فيه أكثر من وحدة.

الأداة ما بتكتب ولا بتعدّل أي شي بأودو: بتستدعي بس search/read/fields_get.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date

DEFAULT_URL = "https://bakery.brioche.sa"
READ_ONLY_METHODS = {"search", "search_read", "read", "fields_get", "context_get"}


class OdooClient:
    def __init__(self, url: str | None = None, api_key: str | None = None, db: str | None = None):
        self.url = (url or os.environ.get("ODOO_URL") or DEFAULT_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("ODOO_API_KEY")
        self.db = db or os.environ.get("ODOO_DB")
        if not self.api_key:
            sys.exit("ما في ODOO_API_KEY بالبيئة. أضفه كمتغير بيئة (مش بالكود).")

    def call(self, model: str, method: str, **kwargs):
        if method not in READ_ONLY_METHODS:
            raise ValueError(f"الأداة للقراءة فقط، ممنوع {method}")
        headers = {"Authorization": f"bearer {self.api_key}", "Content-Type": "application/json"}
        if self.db:
            headers["X-Odoo-Database"] = self.db
        req = urllib.request.Request(f"{self.url}/json/2/{model}/{method}",
                                     data=json.dumps(kwargs).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            sys.exit(f"أودو رجّع خطأ {e.code} على {model}.{method}: {body}")
        except urllib.error.URLError as e:
            sys.exit(f"ما قدرت أوصل لأودو على {self.url}: {e.reason}")


def _m2o_name(v) -> str:
    return v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else ""


def _chunks(ids, n=200):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def company_policy(client: OdooClient, company_id: int) -> dict:
    """يقرأ سياسة الائتمان من إعدادات الشركة (موديول eh_credit) إذا موجودة."""
    fields = client.call("res.company", "fields_get", attributes=["type"])
    wanted = ["name", "eh_b2b_overdue_days", "eh_b2b_cash_tolerance_amount", "eh_b2b_cash_tolerance_days"]
    rec = client.call("res.company", "read", ids=[company_id], fields=[f for f in wanted if f in fields],
                      context={"allowed_company_ids": [company_id]})[0]
    return {
        "name": rec.get("name", str(company_id)),
        "suspend_days": rec.get("eh_b2b_overdue_days"),
        "cash_tol_amount": rec.get("eh_b2b_cash_tolerance_amount"),
        "cash_tol_days": rec.get("eh_b2b_cash_tolerance_days"),
    }


def default_company(client: OdooClient) -> int:
    uid = client.call("res.users", "context_get")["uid"]
    return client.call("res.users", "read", ids=[uid], fields=["company_id"])[0]["company_id"][0]


def _receivable_lines(client: OdooClient, company_id: int) -> list[dict]:
    return client.call(
        "account.move.line", "search_read",
        domain=[("account_type", "=", "asset_receivable"), ("parent_state", "=", "posted"),
                ("reconciled", "=", False), ("company_id", "=", company_id), ("amount_residual", "!=", 0)],
        fields=["partner_id", "move_name", "date", "date_maturity", "amount_residual"],
        context={"allowed_company_ids": [company_id]},
    )


def _commercial_map(client: OdooClient, partner_ids, company_id: int, fields: list[str]) -> dict[int, dict]:
    out = {}
    for chunk in _chunks(sorted(partner_ids)):
        for p in client.call("res.partner", "read", ids=chunk, fields=fields,
                             context={"allowed_company_ids": [company_id], "active_test": False}):
            out[p["id"]] = p
    return out


def load_from_odoo(client: OdooClient, company_id: int, other_company_id: int | None = None,
                   exclude: str | None = None, today: date | None = None):
    """يرجع (customers, invoices, other_balances) بنفس شكل load() تبع الملفات.

    customers: {اسم: {name, credit_limit, payment_days, [cash_tol_amount, cash_tol_days]}}
    invoices:  {اسم: [Invoice]}  = البنود المدينة المفتوحة (فواتير، ودفعات/إشعارات غير مسوّاة بالسالب)
    other_balances: {اسم: (رصيد، أقصى تأخير)} من شركة ثانية للعلم فقط (مثلاً الشركة القديمة)
    """
    from credit_check import Invoice, parse_date, parse_payment_days

    ctx = {"allowed_company_ids": [company_id]}
    pfields_all = client.call("res.partner", "fields_get", attributes=["type"])
    base = ["name", "commercial_partner_id", "credit_limit", "property_payment_term_id"]
    extra = ["eh_cash_tolerance_override", "eh_cash_tolerance_amount", "eh_cash_tolerance_days"]
    fields = base + [f for f in extra if f in pfields_all]

    lines = _receivable_lines(client, company_id)
    # العملاء: كل من عليه بند مفتوح + كل من له حد ائتماني بهالشركة
    scope = {l["partner_id"][0] for l in lines if l["partner_id"]}
    scope |= set(client.call("res.partner", "search", domain=[("credit_limit", ">", 0)], context=ctx))
    if "eh_is_b2b" in pfields_all:
        scope |= set(client.call("res.partner", "search", domain=[("eh_is_b2b", "=", True)], context=ctx))
    partners = _commercial_map(client, scope, company_id, fields)
    commercial_ids = {p["commercial_partner_id"][0] for p in partners.values()}
    missing = commercial_ids - partners.keys()
    if missing:
        partners.update(_commercial_map(client, missing, company_id, fields))

    skip = re.compile(exclude) if exclude else None
    names: dict[int, str] = {}
    customers: dict[str, dict] = {}
    for cid in sorted(commercial_ids):
        p = partners[cid]
        if skip and skip.search(p["name"] or ""):
            continue
        name = p["name"] or f"#{cid}"
        if name in customers:  # نفس الاسم لعميلين مختلفين
            name = f"{name} (#{cid})"
        names[cid] = name
        c = {"name": name, "credit_limit": float(p.get("credit_limit") or 0.0),
             "payment_days": parse_payment_days(_m2o_name(p.get("property_payment_term_id")))}
        if p.get("eh_cash_tolerance_override"):
            c["cash_tol_amount"] = float(p.get("eh_cash_tolerance_amount") or 0.0)
            c["cash_tol_days"] = int(p.get("eh_cash_tolerance_days") or 0)
        customers[name] = c

    invoices = {n: [] for n in customers}
    for l in lines:
        if not l["partner_id"]:
            continue
        p = partners.get(l["partner_id"][0])
        name = names.get(p["commercial_partner_id"][0]) if p else None
        if name is None:
            continue
        invoices[name].append(Invoice(number=l.get("move_name") or "", invoice_date=parse_date(l.get("date")),
                                      due_date=parse_date(l.get("date_maturity")),
                                      amount_due=float(l["amount_residual"])))

    other: dict[str, tuple[float, int]] = {}
    if other_company_id:
        olines = _receivable_lines(client, other_company_id)
        omap = _commercial_map(client, {l["partner_id"][0] for l in olines if l["partner_id"]},
                               other_company_id, ["commercial_partner_id"])
        today = today or date.today()
        for l in olines:
            if not l["partner_id"]:
                continue
            name = names.get(omap[l["partner_id"][0]]["commercial_partner_id"][0])
            if name is None:
                continue
            bal, days = other.get(name, (0.0, 0))
            due = parse_date(l.get("date_maturity"))
            if l["amount_residual"] >= 1 and due:
                days = max(days, (today - due).days)
            other[name] = (bal + l["amount_residual"], days)
    return customers, invoices, other
