# فحص ائتمان عملاء المخبز

يقولك لكل عميل: **مسموح** أبيعه آجل، **انتبه**، **كاش فقط**، أو **ممنوع**، حسب حده الائتماني وأيام السداد.

## القواعد

| العميل | الحالة | متى |
|---|---|---|
| آجل (حده > 0) | ممنوع | متأخر أكثر من `--suspend-days` يوم عن الاستحقاق، أو رصيده وصل الحد أو تجاوزه |
| آجل | انتبه | عليه متأخر ضمن `--suspend-days`، أو مستهلك 80% أو أكثر من حده |
| آجل | مسموح | غير هيك، ويطلع لك المبلغ المتاح |
| كاش (حده صفر) | ممنوع | عليه فواتير متأخرة أكثر من مبلغ وأيام التسامح |
| كاش | كاش فقط | غير هيك: البيع بالدفع عند الاستلام |

- تاريخ الاستحقاق ينقرأ من الفاتورة. إذا كان فاضي، ينحسب: تاريخ الفاتورة + أيام السداد تبع العميل.
- الدفعات والإشعارات الدائنة غير المسوّاة تنخصم من أقدم الفواتير المتأخرة.
- المبالغ أقل من ريال (فروقات تقريب) بتنتجاهل.

## السحب المباشر من أودو

```bash
export ODOO_API_KEY=...            # من متغيرات البيئة، لا تحطه بالكود ولا بالمحادثة
# اختياري: ODOO_URL (افتراضي https://bakery.brioche.sa) و ODOO_DB

# فرع المخبز، مع عرض رصيد الشركة القديمة للعلم، بدون حسابات التجربة
python3 credit_check.py --odoo --company 124 --other-company 3 --exclude '^ZZ TEST' --out report.xlsx

# عميل واحد مع طلب جديد
python3 credit_check.py --odoo --customer "كيمبو" --order 500
```

- الأداة **للقراءة بس**: بتستدعي search/read/fields_get، وما بتغيّر شي بأودو.
- بتقرأ سياسة الشركة من إعداداتها: أيام الإيقاف التلقائي (`eh_b2b_overdue_days`) وتسامح الكاش، وتسامح العميل الخاص إذا مفعّل له.
- العملاء = كل من له حد ائتماني أو مصنّف B2B أو عليه رصيد مفتوح بالشركة. جهات الاتصال الفرعية بتنضم لشركتها الأم.
- `--other-company` بيعرض رصيد شركة ثانية (مثلاً الشركة القديمة شغف الخبز، رقم 3) **للعلم بس**، وما بيأثر على القرار.

## الملفات المطلوبة (من Odoo)

1. **العملاء** (Contacts ← Export): `Name`، `Credit Limit`، `Customer Payment Terms`
2. **الفواتير المفتوحة** (Invoicing ← Customers ← Invoices، فلتر: Posted + Not Paid/Partially Paid ← Export):
   `Number`، `Customer`، `Invoice Date`، `Due Date`، `Amount Due`

ينفع CSV أو Excel، والأعمدة بالعربي كمان مقبولة (العميل، الحد الائتماني، أيام السداد، تاريخ الاستحقاق، المبلغ المتبقي).

## التشغيل

```bash
# كل العملاء + تقرير Excel ملوّن
python3 credit_check.py --customers customers.xlsx --invoices invoices.xlsx --out report.xlsx

# عميل واحد مع طلب جديد بقيمة 6000: يمشي ولا لا؟
python3 credit_check.py --customers customers.xlsx --invoices invoices.xlsx --customer "الريحان" --order 6000

# إعطاء مهلة 7 أيام بعد الاستحقاق قبل المنع، والتنبيه من 90%
python3 credit_check.py --customers customers.xlsx --invoices invoices.xlsx --grace-days 7 --warn-usage 0.9
```

جرّبه على البيانات الوهمية: `python3 credit_check.py --customers sample/customers.csv --invoices sample/invoices.csv --today 2026-11-03`

الاختبارات: `python3 -m unittest discover -s tests`
