from __future__ import annotations

from datetime import date

SIGN_ORDER = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]

SIGN_NAMES = {
    "ru": {
        "aries": "ОВЕН", "taurus": "ТЕЛЕЦ", "gemini": "БЛИЗНЕЦЫ", "cancer": "РАК",
        "leo": "ЛЕВ", "virgo": "ДЕВА", "libra": "ВЕСЫ", "scorpio": "СКОРПИОН",
        "sagittarius": "СТРЕЛЕЦ", "capricorn": "КОЗЕРОГ", "aquarius": "ВОДОЛЕЙ", "pisces": "РЫБЫ",
    },
    "en": {
        "aries": "ARIES", "taurus": "TAURUS", "gemini": "GEMINI", "cancer": "CANCER",
        "leo": "LEO", "virgo": "VIRGO", "libra": "LIBRA", "scorpio": "SCORPIO",
        "sagittarius": "SAGITTARIUS", "capricorn": "CAPRICORN", "aquarius": "AQUARIUS", "pisces": "PISCES",
    },
}

RU_MONTHS = {
    1: "ЯНВАРЯ", 2: "ФЕВРАЛЯ", 3: "МАРТА", 4: "АПРЕЛЯ", 5: "МАЯ", 6: "ИЮНЯ",
    7: "ИЮЛЯ", 8: "АВГУСТА", 9: "СЕНТЯБРЯ", 10: "ОКТЯБРЯ", 11: "НОЯБРЯ", 12: "ДЕКАБРЯ",
}
EN_MONTHS = {
    1: "JANUARY", 2: "FEBRUARY", 3: "MARCH", 4: "APRIL", 5: "MAY", 6: "JUNE",
    7: "JULY", 8: "AUGUST", 9: "SEPTEMBER", 10: "OCTOBER", 11: "NOVEMBER", 12: "DECEMBER",
}

CONTENT_FIELDS = ("card", "general", "love", "career_money", "advice")
DIVERSITY_META_FIELDS = ("theme", "situation", "tone", "advice_pattern")

FIELD_LIMITS = {
    "card": (80, 220),
    "general": (90, 420),
    "love": (50, 260),
    "career_money": (50, 260),
    "advice": (30, 180),
}


def display_date(value: date, language: str) -> str:
    months = RU_MONTHS if language == "ru" else EN_MONTHS
    return f"{value.day} {months[value.month]} {value.year}"
