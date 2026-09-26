from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from kaban.ai.openai import AIProviderError, StructuredJsonClient
from kaban.ai.usage import UsageTracker
from projects.caelus.domain import CONTENT_FIELDS, SIGN_ORDER
from projects.caelus.schemas import output_schema, single_field_output_schema, single_sign_output_schema


ProviderError = AIProviderError


class ContentProvider(Protocol):
    name: str

    def generate(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]: ...

    def generate_sign(self, *, sign: str, system_prompt: str, user_prompt: str) -> dict[str, Any]: ...

    def generate_field(self, *, sign: str, field: str, system_prompt: str, user_prompt: str) -> str: ...


@dataclass
class OpenAIProvider:
    model: str
    reasoning_effort: str = "low"
    name: str = "openai"
    usage: UsageTracker = field(init=False)
    _client: StructuredJsonClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = StructuredJsonClient(self.model, reasoning_effort=self.reasoning_effort)
        self.usage = self._client.usage

    def _request(self, *, system_prompt: str, user_prompt: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        return self._client.request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            schema_name=schema_name,
        )

    def generate(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = self._request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=output_schema(),
            schema_name="caelus_daily_forecasts",
        )
        return payload["signs"]

    def generate_sign(self, *, sign: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if sign not in SIGN_ORDER:
            raise ProviderError(f"Неизвестный знак: {sign}")
        payload = self._request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=single_sign_output_schema(),
            schema_name="caelus_single_forecast",
        )
        return payload["forecast"]

    def generate_field(self, *, sign: str, field: str, system_prompt: str, user_prompt: str) -> str:
        if sign not in SIGN_ORDER:
            raise ProviderError(f"Неизвестный знак: {sign}")
        if field not in CONTENT_FIELDS:
            raise ProviderError(f"Неизвестное поле: {field}")
        payload = self._request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=single_field_output_schema(field),
            schema_name=f"caelus_{field}_rewrite",
        )
        return str(payload["text"]).strip()


class MockProvider:
    name = "mock"

    def __init__(self, language: str):
        self.language = language
        self._regeneration_counters: dict[str, int] = {}

    def generate(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        del system_prompt, user_prompt
        return _mock_signs(self.language)

    def generate_sign(self, *, sign: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        del system_prompt, user_prompt
        if sign not in SIGN_ORDER:
            raise ProviderError(f"Неизвестный знак: {sign}")
        attempt = self._regeneration_counters.get(sign, 0) + 1
        self._regeneration_counters[sign] = attempt
        base = dict(_mock_signs(self.language)[sign])
        return _mock_regenerated_sign(base, self.language, sign, attempt)

    def generate_field(self, *, sign: str, field: str, system_prompt: str, user_prompt: str) -> str:
        del system_prompt, user_prompt
        if sign not in SIGN_ORDER:
            raise ProviderError(f"Неизвестный знак: {sign}")
        if field not in CONTENT_FIELDS:
            raise ProviderError(f"Неизвестное поле: {field}")
        key = f"{sign}:{field}"
        attempt = self._regeneration_counters.get(key, 0) + 1
        self._regeneration_counters[key] = attempt
        base = dict(_mock_signs(self.language)[sign])
        return _mock_regenerated_sign(base, self.language, sign, attempt)[field]



def _mock_regenerated_sign(base: dict[str, str], language: str, sign: str, attempt: int) -> dict[str, str]:
    """Даёт mock provider альтернативный текст для проверки regeneration workflow."""
    sign_index = SIGN_ORDER.index(sign)
    if language == "ru":
        leads = [
            "Сместите фокус с привычного сценария на одну новую деталь.",
            "Проверьте альтернативный маршрут, прежде чем возвращаться к старому плану.",
            "Небольшая смена контекста поможет увидеть ситуацию иначе.",
            "Один практический эксперимент сегодня полезнее повторения знакомой схемы.",
        ]
        endings = [
            "Выберите конкретный следующий шаг и оцените результат вечером.",
            "Сохраните свободу изменить решение после новых фактов.",
            "Не копируйте вчерашний подход только потому, что он знаком.",
            "Сделайте вывод после действия, а не до него.",
        ]
    else:
        leads = [
            "Shift attention from the familiar script to one new detail.",
            "Test an alternative route before returning to the old plan.",
            "A small change of context can reveal a different angle.",
            "One practical experiment is more useful than repeating a familiar pattern.",
        ]
        endings = [
            "Choose one concrete next step and review the result later.",
            "Keep enough flexibility to revise the decision when new facts appear.",
            "Do not repeat yesterday's approach simply because it feels familiar.",
            "Let the action produce evidence before you settle on a conclusion.",
        ]
    idx = (sign_index + attempt - 1) % len(leads)
    end_idx = (sign_index * 2 + attempt) % len(endings)
    lead = leads[idx]
    ending = endings[end_idx]
    # Меняем композицию каждого поля, а не добавляем технический суффикс к старому тексту.
    card_parts = [part.strip() for part in base["card"].split(".") if part.strip()]
    card_detail = card_parts[-1] + "." if card_parts else base["card"]
    general_parts = [part.strip() for part in base["general"].split(".") if part.strip()]
    general_detail = general_parts[-1] + "." if general_parts else base["general"]
    tone_cycle = ("energetic", "calm", "curious", "reflective")
    return {
        "card": f"{lead} {card_detail}",
        "general": f"{lead} {general_detail} {ending}",
        "love": f"{base['love']} {ending}",
        "career_money": f"{lead} {base['career_money']}",
        "advice": f"{ending} {base['advice']}",
        "_diversity": {
            "theme": f"{sign}:regenerated-theme-{attempt}",
            "situation": f"{sign}:regenerated-situation-{attempt}",
            "tone": tone_cycle[(sign_index + attempt) % len(tone_cycle)],
            "advice_pattern": f"{sign}:regenerated-advice-{attempt}",
        },
    }

def _mock_signs(language: str) -> dict[str, Any]:
    if language == "ru":
        rows = {
            "aries": ("Неожиданный поворот может оказаться полезнее привычного плана. Оставьте место для быстрой, но обдуманной реакции.", "Темп дня подталкивает к действию, но лучший результат даст точный выбор момента. Выберите одну инициативу, где скорость действительно важна.", "Прямота поможет быстрее понять позицию другого человека, если оставить место для его ответа.", "Сосредоточьтесь на задаче, которую давно откладывали; лишняя суета только рассеет внимание.", "Действуйте смело, но не автоматически."),
            "taurus": ("То, что развивается медленно, может показать первые признаки результата. Не ускоряйте процесс только ради ощущения движения.", "День подходит для укрепления уже начатого. Надёжность окажется полезнее эффектного, но непродуманного шага.", "Спокойный разговор поможет снять напряжение без лишних объяснений.", "Проверьте детали и ресурсы прежде, чем брать на себя новое обязательство.", "Сохраните темп, который можете поддерживать долго."),
            "gemini": ("Одна фраза или случайная встреча способна запустить цепочку новых идей. Запишите то, что действительно зацепит внимание.", "Информации будет много, и главное — отличить важное от просто интересного. Любопытство сработает лучше вместе с фильтром приоритетов.", "Не додумывайте за собеседника: один уточняющий вопрос сэкономит массу энергии.", "Хороший момент для переговоров, обучения и задач, где нужна гибкость мышления.", "Сначала уточните, потом делайте вывод."),
            "cancer": ("Знакомая ситуация может вызвать новое чувство и подсказать, что пора изменить отношение к ней. Не игнорируйте этот сигнал.", "Внешне день может казаться обычным, но внутренние реакции будут особенно информативны. Дайте себе время понять их без поспешных решений.", "Мягкость не мешает обозначать границы; эти две вещи хорошо сочетаются.", "Не берите чужую срочность за свою — расставьте задачи по собственным приоритетам.", "Защитите то, что действительно важно."),
            "leo": ("Ваше присутствие заметнее обычного. Используйте внимание не ради эффекта, а чтобы продвинуть идею, которая действительно важна.", "Есть шанс оказаться в центре события или разговора. Уверенность сработает лучше, если к ней добавить щедрость к другим.", "Тёплая инициатива может заметно изменить атмосферу в отношениях.", "Покажите результат, а не только намерение — конкретика усилит вашу позицию.", "Сияйте так, чтобы рядом становилось светлее и другим."),
            "virgo": ("Небольшое несоответствие может подсказать, где скрывается настоящее решение. Внимание к деталям сегодня окупится.", "День поддерживает системность: порядок в мелочах освободит пространство для более важных решений. Ищите причину, а не симптом.", "Не превращайте заботу в исправление другого человека — иногда достаточно услышать.", "Пересмотрите процесс, а не пытайтесь работать быстрее внутри неудобной системы.", "Упростите то, что стало сложнее необходимого."),
            "libra": ("Выбор станет проще, когда перестанете искать вариант, который устроит абсолютно всех. Сверьтесь со своими приоритетами.", "Тема баланса выйдет на первый план, но равновесие не всегда означает компромисс. Иногда оно требует ясной позиции.", "Вежливость и честность отлично работают вместе — не заменяйте одно другим.", "Сравните условия спокойно и не соглашайтесь только ради того, чтобы быстрее закрыть вопрос.", "Ищите не идеальный баланс, а честный."),
            "scorpio": ("То, что раньше выглядело неоднозначно, может начать складываться в понятную картину. Не спешите раскрывать свои выводы.", "Наблюдательность станет главным ресурсом. Чем меньше вы реагируете на первый импульс, тем больше заметите в деталях.", "Глубокий разговор возможен, если не превращать его в проверку или допрос.", "Сосредоточьтесь на задаче, где требуется концентрация и умение видеть скрытые зависимости.", "Не всё важное нужно озвучивать сразу."),
            "sagittarius": ("Новая возможность может появиться там, где вы ожидали лишь обычную рутину. Будьте готовы немного изменить маршрут.", "День любит движение, обучение и расширение привычных рамок. Главное — выбрать направление, а не распыляться на всё сразу.", "Совместный опыт сблизит сильнее долгих объяснений — предложите сделать что-то вместе.", "Идеи перспективны, но перед стартом проверьте один практический момент, который легко упустить.", "Смотрите дальше, но помните о следующем шаге."),
            "capricorn": ("План, который казался слишком медленным, начинает показывать свою силу. Продолжайте без резких и лишних перестроек.", "Последовательность даст больше, чем рывок. Маленькое завершённое действие ценнее большого списка намерений.", "Надёжность будет восприниматься как проявление заботы сильнее красивых слов.", "Закройте один незавершённый вопрос прежде, чем брать новый — это освободит время и внимание.", "Стройте то, что выдержит проверку временем."),
            "aquarius": ("Странная на первый взгляд идея может оказаться самой перспективной. Не отбрасывайте её до того, как спокойно проверите.", "Привычные решения могут казаться тесными. Посмотрите на задачу с непривычной стороны или обсудите её с новым человеком.", "Дайте другому человеку право удивить вас вместо того, чтобы заранее угадывать реакцию.", "Экспериментируйте небольшими шагами: проверка гипотезы лучше долгих размышлений о ней.", "Оставьте место для неожиданного решения."),
            "pisces": ("Тихая мысль может оказаться важнее самого громкого события дня. Не заполняйте каждую паузу делами и разговорами.", "Чувствительность помогает замечать нюансы, которые обычно ускользают. Отличайте интуицию от тревожных фантазий и проверяйте факты.", "Слушайте не только слова, но и общий тон общения; при этом не делайте выводов без проверки.", "Творческая задача пойдёт легче, если сначала убрать внешние отвлекающие факторы.", "Дайте себе немного тишины перед важным решением."),
        }
    else:
        rows = {
            "aries": ("A sudden change of pace could work in your favor. Keep enough room in the plan to respond quickly without acting on impulse.", "Momentum is available, but timing matters more than sheer speed. Choose the one initiative that genuinely benefits from decisive action.", "Directness can clear the air if you leave equal room for the other person to answer.", "Tackle the task you have been postponing rather than scattering energy across several new ones.", "Move boldly, not automatically."),
            "taurus": ("Something slow-moving may finally show evidence of progress. Resist the urge to force the pace simply to feel more active.", "Steady work has more value than a dramatic reset. Strengthen what is already sound before adding another commitment.", "A calm conversation can lower tension without requiring a long explanation.", "Check resources, timing, and practical details before saying yes to an extra obligation.", "Choose a pace you can sustain."),
            "gemini": ("A passing remark or unexpected conversation could open a useful line of thought. Capture the idea before the day becomes noisy.", "Information arrives quickly, so discernment matters. Curiosity works best when you separate what is important from what is merely interesting.", "Ask one clarifying question instead of filling the gaps with assumptions.", "Negotiation, learning, and flexible problem-solving are especially productive areas for your attention.", "Clarify first; conclude second."),
            "cancer": ("A familiar situation may stir a different feeling than usual. Treat that reaction as information rather than something to brush aside.", "The surface of the day may look ordinary while your inner response tells a more useful story. Give yourself time to interpret it accurately.", "Warmth and clear boundaries can coexist; you do not need to choose between them.", "Do not adopt someone else’s urgency as your own. Set priorities according to what actually matters to you.", "Protect what deserves your energy."),
            "leo": ("You may draw more attention than expected. Use the visibility to advance something meaningful rather than simply making an impression.", "A conversation or project can put you near the center of events. Confidence lands best when it includes generosity and room for others.", "A warm initiative from you could noticeably change the emotional climate.", "Show concrete progress rather than relying on intention; evidence will strengthen your position.", "Let your confidence create space for others too."),
            "virgo": ("A small mismatch could reveal the real source of a problem. Careful observation is likely to save more time than rushing ahead.", "Systematic thinking is your advantage. Tidying a process or fixing one weak link can create room for a more important decision.", "Care does not have to become correction; sometimes listening is the more useful response.", "Improve the process instead of trying to work faster inside a system that keeps creating friction.", "Simplify what has become unnecessarily complicated."),
            "libra": ("A decision gets easier once you stop looking for the option that pleases everyone. Bring your own priorities back into the equation.", "Balance is the theme, but balance does not always mean compromise. One situation may require a clear position rather than a midpoint.", "Courtesy and honesty can work together; neither needs to replace the other.", "Compare terms carefully and avoid agreeing merely to close the matter quickly.", "Aim for an honest balance, not a perfect one."),
            "scorpio": ("A situation that felt ambiguous may begin to form a clearer pattern. Observe a little longer before revealing every conclusion.", "Your advantage is concentration rather than speed. The less you react to the first signal, the more useful detail you are likely to notice.", "A deeper conversation is possible if curiosity does not turn into interrogation.", "Give your best focus to work that rewards pattern recognition, research, or careful diagnosis.", "Not every insight needs to be shared immediately."),
            "sagittarius": ("An ordinary task may contain a doorway to something more interesting. Stay willing to alter the route when a better option appears.", "Movement and learning are productive, provided you choose a direction. Too many parallel possibilities will dilute the benefit.", "A shared experience may create more closeness than another long explanation.", "A promising idea deserves one practical check before you commit resources or announce the plan.", "Look farther ahead, but take the next step clearly."),
            "capricorn": ("A plan that once felt slow may begin to prove its strength. Keep building instead of redesigning the whole structure too soon.", "Consistency is the useful force here. One completed action creates more leverage than a long list of intentions.", "Reliability may communicate care more convincingly than polished words.", "Finish one open loop before accepting another; the recovered attention will matter as much as the saved time.", "Build for durability, not applause."),
            "aquarius": ("The idea that seems slightly odd may deserve a closer look. Test it on a small scale before dismissing it as impractical.", "Familiar solutions can feel restrictive. A different perspective or an unexpected collaborator may expose an option you had not considered.", "Leave room for someone to surprise you rather than predicting their response in advance.", "Run a small experiment. A real test will teach you more than another round of abstract debate.", "Keep a door open for the unconventional answer."),
            "pisces": ("A quiet thought may carry more weight than the loudest event around you. Leave a little space that is not filled with input or activity.", "Sensitivity helps you notice subtleties, but it works best alongside reality checks. Separate intuition from the stories anxiety can invent.", "Listen to tone as well as words, while still checking assumptions before treating them as facts.", "Creative work becomes easier after you remove one or two sources of distraction from the environment.", "Give an important decision a moment of quiet."),
        }
    tone_cycle_ru = ("энергичный", "спокойный", "любознательный", "рефлексивный")
    tone_cycle_en = ("energetic", "calm", "curious", "reflective")
    tone_cycle = tone_cycle_ru if language == "ru" else tone_cycle_en
    result: dict[str, Any] = {}
    for sign, vals in rows.items():
        if sign not in SIGN_ORDER:
            continue
        idx = SIGN_ORDER.index(sign)
        result[sign] = {
            "card": vals[0],
            "general": vals[1],
            "love": vals[2],
            "career_money": vals[3],
            "advice": vals[4],
            "_diversity": {
                "theme": f"{sign}:daily-focus",
                "situation": f"{sign}:distinct-daily-scenario",
                "tone": tone_cycle[idx % len(tone_cycle)],
                "advice_pattern": f"{sign}:practical-next-step",
            },
        }
    return result
