# فحص ائتمان عملاء المخبز

يقولك لكل عميل: **مسموح** أبيعه آجل، **انتبه**، **كاش فقط**، أو **ممنوع**، حسب حده الائتماني وأيام السداد.

## القواعد

| الحالة | متى |
|---|---|
| ممنوع | عنده فاتورة عدّى تاريخ استحقاقها (بعد مهلة السماح)، أو رصيده المفتوح وصل الحد أو تجاوزه |
| كاش فقط | حده الائتماني صفر أو فاضي، وما عليه شي متأخر |
| انتبه | مسموح، بس مستهلك 80% أو أكثر من حده |
| مسموح | غير هيك، ويطلع لك المبلغ المتاح |

تاريخ الاستحقاق ينقرأ من الفاتورة. إذا كان فاضي، ينحسب: تاريخ الفاتورة + أيام السداد تبع العميل.

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
