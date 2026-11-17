# ربط 5 خوادم MCP مع Claude Code

الإعداد كله في ملف [`.mcp.json`](./.mcp.json) على مستوى المشروع. ما في أي مفتاح سرّي داخل الملف: المفاتيح بتتقرأ من متغيرات البيئة.

## الحالة الحالية (نتيجة الاختبار الفعلي)

| # | MCP | كيف يشتغل | يحتاج | الحالة |
|---|-----|-----------|-------|--------|
| 1 | **Perplexity** | `npx @perplexity-ai/mcp-server` (حزمة Perplexity الرسمية) | `PERPLEXITY_API_KEY` + فتح `api.perplexity.ai` | ⚠️ الخادم سليم (4 أدوات) لكن ما يبدأ بدون مفتاح، ولا انختبر استدعاء حقيقي |
| 2 | **Playwright** | `npx @playwright/mcp` (Microsoft) مع Chromium الموجود بالبيئة | ولا شي | ✅ اشتغل: navigate / click / evaluate / screenshot (25 أداة) |
| 3 | **Firecrawl** | `npx firecrawl-mcp` (Firecrawl الرسمية) | `FIRECRAWL_API_KEY` + فتح `api.firecrawl.dev` | ⚠️ الخادم يبدأ (26 أداة) لكن ما انختبر scrape حقيقي |
| 4 | **Higgsfield** | خادم بعيد `https://mcp.higgsfield.ai/mcp` (OAuth) | اشتراك مدفوع + تسجيل دخول + فتح `mcp.higgsfield.ai` | ❌ ما انختبر: النطاق محظور من البيئة |
| 5 | **Chrome DevTools** | `npx chrome-devtools-mcp` (Google) مع Chromium الموجود بالبيئة | ولا شي | ✅ اشتغل: new_page / evaluate_script / console / snapshot / screenshot (32 أداة) |

الاختبار تم بتشغيل نفس الأمر والوسائط الموجودة بـ `.mcp.json` والتحدث معه ببروتوكول MCP مباشرة على صفحة محلية. Perplexity جرّبته بمفتاح وهمي للـ handshake فقط.

## التفعيل (مرة وحدة)

**1. الموافقة على الخوادم.** أي خادم MCP على مستوى المشروع ما يشتغل قبل ما توافق عليه (حماية من مستودعات غريبة). حالياً كلهم `Pending approval`. وافق عليهم من `/mcp` أول ما تفتح جلسة جديدة بهذا المستودع.

**2. المفاتيح (Perplexity وFirecrawl).** من إعدادات بيئة السحابة (قائمة البيئة بعنوان الجلسة ← Edit) أضف متغيرين:

- `PERPLEXITY_API_KEY`
- `FIRECRAWL_API_KEY`

الجلسات الجديدة تلتقطهم تلقائياً. **لا تلصق المفاتيح بالمحادثة أبداً.**

**3. الشبكة.** سياسة الشبكة الحالية ترفض هذي النطاقات. من نفس قائمة Edit ← Network access أضفها بـ Allowed domains:

- `api.perplexity.ai`
- `api.firecrawl.dev`
- `mcp.higgsfield.ai` (وغالباً نطاقات تسجيل الدخول التابعة لـ Higgsfield، ما قدرت أتأكد منها)

الـ browsers (Playwright وChrome DevTools) يمروا بنفس الـ proxy، يعني بس يفتحوا مواقع نطاقاتها مسموحة. جربت: `github.com` انفتح بالاثنين، و`example.com` انرفض (`ERR_TUNNEL_CONNECTION_FAILED`). لو بدك تفحص موقع معين، أضف نطاقه بنفس الطريقة، **مع نطاقات الـ CDN والـ assets التابعة له**. مثال: `github.com` انفتح لكن `github.githubassets.com` انرفض، فملفات JS ما تحمّلت (أكثر من 100 خطأ `ERR_TUNNEL_CONNECTION_FAILED`)، وبهالحالة نتائج Lighthouse ما تعبّر عن جودة الموقع الحقيقية.

**4. Higgsfield.** حسب صفحتهم الرسمية: اشتراك مدفوع، وفي Claude تضيفه كـ custom connector بالعنوان `https://mcp.higgsfield.ai/mcp` من <https://claude.ai/customize/connectors> (تسجيل الدخول OAuth بنافذة منهم). لمستخدمي Claude Code الصفحة نفسها بتوصي بأداة سطر الأوامر تبعتهم (Higgsfield CLI) بدل الـ MCP. ما قدرت أفتح صفحتهم مباشرة، فتأكد من الخطوات الحالية عندهم قبل الاعتماد عليها.

**5. تأكد.** `claude mcp list` أو `/mcp` داخل الجلسة. المتوقع: Playwright وChrome DevTools متصلين دايماً، والباقي حسب الخطوات فوق.

> بديل لـ Firecrawl: موجود كـ connector جاهز بـ claude.ai (OAuth بدون مفتاح بالبيئة) من نفس صفحة الـ connectors. ما جربته من هون.

## كيف تستخدمهم صح

**Perplexity**: للمعلومة الحالية مع مصادر. الأدوات الأربعة من الأخف للأثقل: `perplexity_search` (نتائج + روابط)، `perplexity_ask` (جواب سريع)، `perplexity_reason` (استدلال)، `perplexity_research` (بحث عميق، بطيء وأغلى). ابدأ بالأخف.

**Firecrawl**: عندك رابط معروف ← `firecrawl_scrape` (محتوى نظيف). ما عندك رابط ← `firecrawl_search`. بدك هيكل موقع ← `firecrawl_map`. `firecrawl_crawl` يحرق credits بسرعة، حدّد العمق والعدد دايماً. الأدوات `firecrawl_agent` و`firecrawl_monitor_*` للمهام الأكبر.

**Playwright**: لأتمتة واجهة (تعبئة فورمات، تسلسل ضغطات، اختبار flow). يشتغل على **accessibility snapshot** (`browser_snapshot`) وهو أرخص بالـ tokens وأدق من الصور، فاستخدمه بدل `browser_take_screenshot` إلا لما تحتاج شكل بصري فعلاً. العناصر تتحدد بـ `target` (مو `ref`).

**Chrome DevTools**: للتشخيص: console، network، CSS، `lighthouse_audit`، `performance_start_trace`. كل أداة تخص صفحة بدها `pageId` (يرجع من `new_page`/`list_pages`)، وبدونه ترفض. الـ screenshots مضبوطة JPEG بعرض أقصى 1280 لتوفير السياق.

**Higgsfield**: توليد صور وفيديو بـ credits. اطلب من Claude يأكد معك قبل أي توليد.

**قاعدة عامة**: Playwright للتشغيل والاختبار، Chrome DevTools للتشخيص. لا تشغّل الاثنين على نفس المهمة، كل واحد له Chromium مستقل.

## ملاحظات

- **تشغيل محلي على جهازك**: بـ `.mcp.json` خيارات خاصة ببيئة السحابة (`--headless`، `--no-sandbox`، مسار `/opt/pw-browsers/chromium`). على جهازك احذفها وخلّي Chrome/Chromium العادي.
- **السياق**: الأربعة المحلية لحالها 87 أداة (غير أدوات Higgsfield)، وكل أداة تاخد جزء من سياق المحادثة. عطّل بـ `/mcp` اللي ما بتستخدمه بالمهمة.
- **خصوصية Chrome DevTools**: افتراضياً يرسل إحصائيات استخدام لـ Google، ويرسل روابط من الـ performance traces لـ CrUX. للإيقاف: `--no-usage-statistics` و`--no-performance-crux`.
- **الإصدارات**: Playwright وChrome DevTools مثبتين على `@latest`، يعني أي تحديث يطلع ينزل تلقائياً. لو بدك ثبات حط رقم إصدار.

## المصادر

- Playwright MCP: <https://playwright.dev/mcp/clients/claude-code>
- Chrome DevTools MCP: <https://github.com/ChromeDevTools/chrome-devtools-mcp>
- Firecrawl MCP: <https://docs.firecrawl.dev/mcp-server>
- Perplexity MCP: <https://github.com/perplexityai/modelcontextprotocol>
- Higgsfield: <https://higgsfield.ai/creator-hub/help-center/integrations/how-do-i-connect-higgsfield-to-ai-agent>
