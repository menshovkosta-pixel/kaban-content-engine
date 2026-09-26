from __future__ import annotations

import json
from datetime import date

from .domain import SIGN_NAMES


def _sign_names():
    return SIGN_NAMES


def system_prompt(language: str) -> str:
    if language == "ru":
        return (
            "Ты редактор премиального астрологического медиа CAELUS. Пиши по-русски естественно, "
            "современно, атмосферно и конкретно, без канцелярита и дешёвых эзотерических клише. "
            "Контент развлекательный и рефлексивный: не утверждай, что будущее известно наверняка. "
            "Не придумывай точные положения планет, аспекты, ретроградность, фазы Луны или астрономические события, "
            "если эти данные не даны во входе. Не обещай гарантированный финансовый успех, болезни, смерть, измену "
            "или другие проверяемые события. Каждый из 12 знаков должен заметно отличаться по теме, ситуации, тону, "
            "отношениям, работе/деньгам и совету. Избегай повторяющихся шаблонов и одинаковых первых фраз."
        )
    return (
        "You are the editor of CAELUS, a premium astrology media brand. Write original, natural contemporary English, "
        "not translated-sounding copy. Treat the content as entertainment and reflection, not certain knowledge of the future. "
        "Do not invent exact planetary positions, aspects, retrogrades, lunar phases, or astronomical events unless provided. "
        "Do not promise guaranteed financial outcomes, illness, death, infidelity, or other verifiable events. Make all 12 signs "
        "meaningfully different in theme, situation, tone, relationships, work/money, advice, rhythm, and wording."
    )


def user_prompt(target: date, language: str, history: str) -> str:
    names = _sign_names()[language]
    if language == "ru":
        text = f"""
Создай комплект ежедневных прогнозов CAELUS на {target.isoformat()} для всех 12 знаков.

Для каждого знака верни пять текстовых полей и служебный объект _diversity:
- card: короткий текст карточки; целевой диапазон 120–180 символов, допустимый максимум 220;
- general: основной Telegram-прогноз, 2–4 содержательных предложения;
- love: 1–2 предложения про отношения/общение;
- career_money: 1–2 предложения про работу, задачи или деньги без финансовых гарантий;
- advice: одна короткая практичная мысль дня;
- _diversity: короткие смысловые метки theme, situation, tone, advice_pattern. Они служебные и не должны дублировать весь текст прогноза.

Требования к разнообразию:
- у каждого знака должна быть своя центральная тема и конкретная ситуация;
- не повторяй одинаковые советы, метафоры, конструкции и вступления;
- сделай часть прогнозов спокойными, часть энергичными, часть социальными, часть рефлексивными;
- не начинай всё словом «Сегодня»;
- не добавляй Markdown, эмодзи, название знака или дату внутрь полей;
- не используй технические астрологические утверждения без входных эфемерид.

Названия знаков для контекста: {json.dumps(names, ensure_ascii=False)}
"""
    else:
        text = f"""
Create the CAELUS daily forecast set for {target.isoformat()} for all 12 zodiac signs.

Return five content fields plus an internal _diversity object per sign:
- card: short card copy; target 120–180 characters, hard maximum 220;
- general: main Telegram forecast, 2–4 substantive sentences;
- love: 1–2 sentences about relationships or communication;
- career_money: 1–2 sentences about work, tasks, or money, with no financial guarantees;
- advice: one concise practical thought for the day;
- _diversity: short semantic labels theme, situation, tone, advice_pattern. These are internal metadata and must not repeat the full forecast text.

Diversity requirements:
- give every sign its own central theme and concrete situation;
- do not recycle advice, metaphors, sentence templates, or openings;
- mix calm, energetic, social, and reflective tones;
- do not start everything with “Today”;
- do not include Markdown, emojis, sign names, or dates inside fields;
- do not make technical astrology claims without supplied ephemeris data.

Sign names for context: {json.dumps(names, ensure_ascii=False)}
"""
    if history:
        suffix = (
            "\nНедавние тексты ниже. Избегай повторения не только формулировок, но и центральных идей:\n"
            if language == "ru" else
            "\nRecent copy is below. Avoid repeating both wording and the central ideas:\n"
        )
        text += suffix + history
    return text.strip()


def regenerate_sign_prompt(
    *,
    target: date,
    language: str,
    sign: str,
    current_day_context: str,
    conflicts: str,
    attempt: int,
    current_sign_text: str = "",
) -> str:
    """Prompt для точечной перегенерации одного знака после uniqueness conflict."""
    sign_name = _sign_names()[language][sign]
    if language == "ru":
        return f"""
Перепиши прогноз CAELUS на {target.isoformat()} только для знака {sign_name}.
Это попытка перегенерации №{attempt} после автоматической проверки уникальности.

Верни пять текстовых полей card, general, love, career_money, advice и служебный объект _diversity с theme, situation, tone, advice_pattern.
Сохраняй те же требования к длине и качеству, что и для полного ежедневного набора.

Критически важно:
- придумай НОВУЮ центральную тему и другую конкретную ситуацию;
- не перефразируй конфликтующий текст близкими синонимами;
- измени структуру предложений, совет, отношения и рабочий сценарий;
- не повторяй идеи других знаков этого дня;
- не добавляй Markdown, эмодзи, название знака или дату внутрь полей.

Текущий прогноз этого знака. Его НЕЛЬЗЯ возвращать без изменений и нельзя просто перефразировать:
{current_sign_text or 'Текущий текст недоступен; всё равно требуется полностью новый вариант.'}

Найденные конфликты с предыдущими 90 днями или текущим днём. Эти тексты также запрещено перефразировать:
{conflicts or 'Нет текстового описания конфликта; требуется заметно новый вариант.'}

Контекст других знаков текущего дня, чтобы не повторять их:
{current_day_context}
""".strip()
    return f"""
Rewrite the CAELUS forecast for {sign_name} on {target.isoformat()} only.
This is regeneration attempt #{attempt} after the automated uniqueness check.

Return five content fields card, general, love, career_money, advice plus an internal _diversity object with theme, situation, tone, advice_pattern.
Keep the same length and quality constraints as the full daily set.

Critical requirements:
- create a NEW central theme and a different concrete situation;
- do not merely paraphrase the conflicting copy with synonyms;
- change sentence structure, advice, relationship scenario, and work/money scenario;
- avoid ideas already used by other signs today;
- do not include Markdown, emojis, the sign name, or the date inside fields.

Current forecast for this sign. Do NOT return it unchanged and do not merely paraphrase it:
{current_sign_text or 'The current copy is unavailable; still produce a completely new version.'}

Detected conflicts with the previous 90 days or the current set. Do not paraphrase these texts either:
{conflicts or 'No textual conflict details are available; produce a clearly different version.'}

Other signs today, provided only to avoid repetition:
{current_day_context}
""".strip()


def regenerate_field_prompt(
    *,
    target: date,
    language: str,
    sign: str,
    field: str,
    current_text: str,
    conflicts: str,
    current_day_context: str,
    attempt: int,
) -> str:
    """Prompt для точечной перегенерации только одного поля прогноза."""
    sign_name = _sign_names()[language][sign]
    field_rules_ru = {
        "card": "короткий текст карточки, 120–180 символов предпочтительно, максимум 220",
        "general": "основной Telegram-прогноз, 2–4 содержательных предложения",
        "love": "1–2 предложения про отношения или общение",
        "career_money": "1–2 предложения про работу, задачи или деньги без финансовых гарантий",
        "advice": "одна короткая практичная мысль дня",
    }
    field_rules_en = {
        "card": "short card copy, preferably 120–180 characters, maximum 220",
        "general": "main Telegram forecast, 2–4 substantive sentences",
        "love": "1–2 sentences about relationships or communication",
        "career_money": "1–2 sentences about work, tasks, or money without financial guarantees",
        "advice": "one concise practical thought for the day",
    }
    if language == "ru":
        return f"""
Создай НОВЫЙ вариант только поля {field} для знака {sign_name} на {target.isoformat()}.
Это точечная перегенерация №{attempt}. Остальные поля знака менять нельзя.
Формат поля: {field_rules_ru[field]}.

Текущий текст, который требуется заменить:
{current_text}

Конфликтующие исторические/текущие тексты:
{conflicts or 'Явный конфликт не найден, но пользователь запросил новый вариант.'}

Требования к НОВОМУ содержанию:
- выбери другую центральную тему, а не другую формулировку той же мысли;
- придумай другую конкретную ситуацию или контекст;
- дай другой практический вывод/совет или другой способ действия;
- не сохраняй тот же причинно-следственный сценарий под новыми словами;
- не копируй синтаксис, начало предложения и ключевые образы текущего/исторического текста;
- текст должен быть самостоятельным и естественным, а не выглядеть как перефразирование;
- не добавляй Markdown, эмодзи, название знака или дату внутрь текста.

Другие знаки сегодня для предотвращения повторов именно в поле {field}:
{current_day_context}
""".strip()
    return f"""
Create a NEW version of field {field} only for {sign_name} on {target.isoformat()}.
This is targeted regeneration attempt #{attempt}. Do not modify any other field.
Field format: {field_rules_en[field]}.

Current text that must be replaced:
{current_text}

Conflicting historical/current texts:
{conflicts or 'No explicit conflict was found, but the user requested a new version.'}

Requirements for genuinely NEW content:
- choose a different central theme, not new wording for the same idea;
- use a different concrete situation or context;
- give a different practical conclusion/advice or course of action;
- do not preserve the same cause-and-effect scenario under new words;
- do not copy sentence structure, opening, or key imagery from the current/historical text;
- the result must read as an independent idea, not a paraphrase;
- do not include Markdown, emojis, the sign name, or the date in the text.

Other signs today, used only to avoid repetition in field {field}:
{current_day_context}
""".strip()
