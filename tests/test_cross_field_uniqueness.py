from projects.caelus.validators import has_errors, validate_payload


def _payload(card, general, love, career_money, advice):
    signs = {}
    for sign in (
        "aries", "taurus", "gemini", "cancer", "leo", "virgo",
        "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
    ):
        signs[sign] = {
            "name": sign,
            "card": f"{card} {sign}",
            "general": f"{general} {sign}",
            "love": f"{love} {sign}",
            "career_money": f"{career_money} {sign}",
            "advice": f"{advice} {sign}",
        }
    return {"signs": signs}


def test_same_sign_cross_field_duplicate_is_detected():
    payload = _payload(
        "Сместите фокус с привычного сценария на одну новую деталь.",
        "Общий прогноз совершенно другой.",
        "Отношения требуют отдельного разговора.",
        "Рабочий вопрос требует конкретного решения.",
        "Сохраните свободу изменить решение.",
    )

    payload["signs"]["aries"]["career_money"] = (
        payload["signs"]["aries"]["card"]
    )

    issues = validate_payload(payload)

    conflict = next(
        item for item in issues
        if item["code"] == "cross_field_repetition"
        and item["sign"] == "aries"
        and item["field"] == "career_money"
    )

    assert conflict["severity"] == "error"
    assert conflict["similarity"] >= 0.99


def test_same_sign_repeated_opening_sentence_is_detected():
    opening = (
        "Сместите фокус с привычного сценария на одну новую деталь."
    )

    payload = _payload(
        opening + " Оставьте место для обдуманной реакции.",
        opening + " Выберите одну инициативу и оцените последствия.",
        "В отношениях полезен отдельный спокойный разговор.",
        "В работе сосредоточьтесь на измеримом результате.",
        "Не принимайте решение автоматически.",
    )

    issues = validate_payload(payload)

    assert any(
        item["code"] == "cross_field_repetition"
        and item["sign"] == "aries"
        and item["field"] == "general"
        for item in issues
    )