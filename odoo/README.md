# من نقدر نصدرله فاتورة؟ (شركة الرؤيا الحديثة المتقدمة)

سكربت `invoice_eligibility.py` بيقرأ بس من أودو، وما بيعدل أو يبعت أي شي. بيصنّف كل عميل لشركة الرؤيا الحديثة المتقدمة لوحدة من ثلاث حالات:

| الحكم | متى |
|---|---|
| لا تصدر فاتورة | عليه حظر فوترة، أو رصيده وصل الحد الائتماني أو تجاوزه |
| استكمل البيانات أولاً | الرقم الضريبي ناقص أو غلط (لازم 15 رقم، يبلش ويخلص بـ 3)، أو العنوان الوطني ناقص |
| تصدر فاتورة | كل شي تمام |

وبيكتب ملاحظات كمان: مبلغ الطلبات الجاهزة للفوترة، والفواتير المتأخرة، وحالة العقد والتسجيل، وإذا الفوترة رح تتجاوز الحد الائتماني.

## التشغيل

1. ضيف النطاقات `bakery.brioche.sa` و `www.foodpassion.sa` بـ Network access ← Allowed domains بإعدادات البيئة.
2. ضيف المتغيرات بـ Network secrets: `ODOO_LOGIN` و `ODOO_API_KEY` (مفتاح API من أودو، مش كلمة السر). وإذا الموقع الثاني إله حساب منفصل: `FP_ODOO_LOGIN` و `FP_ODOO_API_KEY`.
3. افتح جلسة جديدة وشغّل:

```bash
python3 odoo/invoice_eligibility.py --url https://bakery.brioche.sa --out bakery.xlsx
python3 odoo/invoice_eligibility.py --url https://www.foodpassion.sa \
  --login-env FP_ODOO_LOGIN --key-env FP_ODOO_API_KEY --out foodpassion.xlsx
```

إذا السكربت ما قدر يعرف قاعدة البيانات لحاله، ضيف `--db <اسم القاعدة>`. وإذا لقى أكثر من شركة بنفس الاسم، ضيف `--company <رقم الشركة>`.
