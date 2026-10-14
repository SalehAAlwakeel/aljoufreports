"""Prompt for Saudi-dialect sentiment classification.

Generic Arabic sentiment models mis-handle Gulf comment threads in three ways
this prompt targets explicitly:

1. **Dialect and slang.** Najdi/Hijazi vocabulary ("وش", "كذا", "يبه", "عسى",
   "هبال", "فشلة") rarely appears in MSA training data.
2. **Sarcasm.** Praise phrasing carrying criticism is extremely common in
   replies to government accounts ("ما شاء الله، وعود من سنتين", "تسلمون 👏👏").
   Taken literally these score Positive, which is exactly backwards.
3. **Prayers and set phrases.** "الله يعطيكم العافية" / "ما شاء الله تبارك
   الله" are genuine praise, while "الله يصلح الحال" / "حسبي الله" are
   complaints. Both look superficially religious and positive.
"""

SYSTEM_PROMPT = """\
أنت محلل مشاعر خبير في اللهجات السعودية (النجدية، الحجازية، الشمالية) \
ومتخصص في تعليقات الجمهور على حسابات الجهات الحكومية السعودية.

مهمتك: تصنيف كل تعليق إلى Positive أو Negative أو Neutral، مع سبب قصير جداً \
بالعربية الفصحى.

قواعد التحليل:
1. اللهجة والعامية: افهم المفردات السعودية الدارجة. أمثلة: "وش ذا" = ما هذا، \
   "كذا" = هكذا، "هبال/هبل" = سخف (سلبي)، "فشلة" = محرج (سلبي)، \
   "يعطيك العافية" = شكر (إيجابي)، "عسى/يا رب" = دعاء، \
   "ما شاء الله تبارك الله" = إعجاب، "تمام/جبتوها" = استحسان.
2. السخرية (مهم جداً): كثير من التعليقات تستخدم صيغة المدح للتعبير عن \
   الانتقاد. إذا كان المدح مصحوباً بإشارة إلى وعود قديمة، أو تأخير، أو تكرار، \
   أو مبالغة غير منطقية، أو تصفيق ساخر (👏 بدون سياق إيجابي)، أو "يا سلام"، \
   أو "ما شاء الله" مع ذكر مشكلة، فالتصنيف Negative وليس Positive. \
   اعتمد على المعنى المقصود لا الحرفي.
3. الأدعية: "الله يعطيكم العافية"، "وفقكم الله"، "الله يسعدكم" = Positive. \
   أما "الله يصلح الحال"، "حسبي الله ونعم الوكيل"، "الله يكفينا شرّكم" \
   = Negative.
4. الأسئلة والاستفسارات المحضة (متى؟ وين؟ كيف أتقدم؟) بدون شكوى = Neutral.
5. الإيموجي جزء من المعنى: ❤️🌹👍🔥 تعزز الإيجابية، 😡🤬👎💔 تعزز السلبية، \
   🙄😂😏 قد تدل على سخرية.
6. التعليقات الدعائية/السبام أو الروابط غير المرتبطة = Neutral.
7. إذا كان التعليق يحمل مدحاً ونقداً معاً، صنّف حسب الغالب؛ وإن تعادلا \
   فاختر Negative لأن النقد يحتاج متابعة.

تنسيق السبب: عبارة قصيرة جداً، 8 كلمات كحد أقصى، بدون حشو. \
ممنوع عبارات مثل "دون أي تعبير عن رأي أو شكوى" أو "يعكس إيجابية". \
اكتب السبب فقط: "ثناء على العمل"، "سخرية من التأخير"، "سؤال عن الوظائف". \
إذا كان التعليق ساخراً ابدأ السبب بكلمة "سخرية".

أعد النتيجة كـ JSON فقط، وأعد عنصراً واحداً لكل تعليق بنفس الـ id المُعطى، \
وبنفس الترتيب، دون إضافة أو حذف."""


# Few-shot examples anchor the sarcasm and prayer-phrase rules, which the model
# otherwise applies inconsistently.
FEW_SHOT_USER = """\
[
 {"id": "x1", "text": "ما شاء الله تبارك الله شغل مرتب وواضح، يعطيكم العافية"},
 {"id": "x2", "text": "يا سلام عليكم 👏👏 نفس الكلام من ثلاث سنوات وما تغير شي"},
 {"id": "x3", "text": "متى يفتح التقديم على الوظائف؟"},
 {"id": "x4", "text": "وش ذا الهبال، خدمة زفت وما حد يرد على الاتصالات 🤬"},
 {"id": "x5", "text": "الله يصلح الحال، الشوارع عندنا في سكاكا ما شافت سفلتة"},
 {"id": "x6", "text": "مشروع الواجهة صار تحفة والله، الجوف تستحق 🌹"}
]"""

FEW_SHOT_ASSISTANT = """\
{"results": [
 {"id": "x1", "label": "Positive", "reason_ar": "ثناء على جودة العمل.", "confidence": 0.97},
 {"id": "x2", "label": "Negative", "reason_ar": "سخرية من تكرار الوعود.", "confidence": 0.93},
 {"id": "x3", "label": "Neutral", "reason_ar": "سؤال عن موعد التقديم.", "confidence": 0.95},
 {"id": "x4", "label": "Negative", "reason_ar": "شكوى من ضعف الخدمة.", "confidence": 0.98},
 {"id": "x5", "label": "Negative", "reason_ar": "شكوى من حالة الشوارع.", "confidence": 0.9},
 {"id": "x6", "label": "Positive", "reason_ar": "إعجاب بالمشروع.", "confidence": 0.96}
]}"""


# Structured-output schema: guarantees parseable results and a closed label set.
RESPONSE_SCHEMA = {
    "name": "sentiment_batch",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "label", "reason_ar", "confidence"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Echo the id supplied for this comment.",
                        },
                        "label": {
                            "type": "string",
                            "enum": ["Positive", "Negative", "Neutral"],
                        },
                        "reason_ar": {
                            "type": "string",
                            "description": "سبب قصير بالعربية، 8 كلمات كحد أقصى، بدون حشو.",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "0.0 - 1.0",
                        },
                    },
                },
            }
        },
    },
}
