from __future__ import annotations

import html
import json
import mimetypes
import os
import subprocess
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from projects.caelus.config import ROOT, load_dotenv, openai_config_message, openai_model, openai_reasoning_effort, telegram_config_message
from projects.caelus import storage as caelus_storage
from projects.caelus.storage import content_path, day_dir, load_json, publication_path, status_path

GENERATED = caelus_storage.GENERATED

def _generated_root() -> Path:
    # Совместимость с v1.13 module-level patching и cloud env override.
    if Path(GENERATED) != Path(caelus_storage.GENERATED):
        return Path(GENERATED)
    return caelus_storage.generated_root()
from projects.caelus.automation import derive_state, run_generation_job, run_publication_job
from projects.caelus.automation_store import iter_automation_targets
from projects.caelus.workflow import (
    approve,
    diversity_settings,
    generate_bundle,
    monthly_api_usage,
    publication_locks_editing,
    preview_diversity,
    regenerate_conflicts,
    regenerate_field,
    regenerate_sign,
    return_to_draft,
    save_diversity_settings,
    save_fields,
    tomorrow_iso,
    uniqueness_rules,
    validation_issues,
)
from projects.caelus.domain import SIGN_ORDER
from projects.caelus.uniqueness.settings import DiversitySettings, HISTORY_OPTIONS, PROFILE_LABELS
from projects.caelus.validators import get_field, has_errors

HOST = "127.0.0.1"
PORT = 8088


def admin_host() -> str:
    return os.getenv("KABAN_ADMIN_HOST", HOST).strip() or HOST


def admin_port() -> int:
    raw = os.getenv("KABAN_ADMIN_PORT", str(PORT)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("KABAN_ADMIN_PORT должен быть целым числом 1..65535") from exc
    if not 1 <= value <= 65535:
        raise ValueError("KABAN_ADMIN_PORT должен быть в диапазоне 1..65535")
    return value


def health_payload() -> dict[str, object]:
    return {"status": "ok", "service": "kaban-admin"}

GLYPHS = {
    "aries": "♈", "taurus": "♉", "gemini": "♊", "cancer": "♋",
    "leo": "♌", "virgo": "♍", "libra": "♎", "scorpio": "♏",
    "sagittarius": "♐", "capricorn": "♑", "aquarius": "♒", "pisces": "♓",
}

CSS = r"""
:root{--bg:#06111c;--panel:#0b1b2a;--gold:#d5aa66;--ivory:#f3e7ce;--muted:#9badbd;--line:#294055;--danger:#d98c8c}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#05101a,#081624);color:var(--ivory);font-family:Arial,sans-serif}
.top{position:sticky;top:0;z-index:10;background:#071522ee;backdrop-filter:blur(14px);border-bottom:1px solid var(--line);padding:16px 24px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.brand{font-family:Georgia,serif;letter-spacing:.28em;color:var(--gold);font-size:22px}.top form,.controls,.actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.top input,.top select{background:#0d2030;border:1px solid var(--line);color:var(--ivory);padding:9px 11px;border-radius:9px}.spacer{flex:1}
.btn{border:1px solid var(--line);background:#10263a;color:var(--ivory);padding:10px 14px;border-radius:9px;cursor:pointer;text-decoration:none;font-weight:650}.btn.primary{background:var(--gold);color:#071522;border-color:var(--gold)}.btn.ok{background:#163729;border-color:#2b6749;color:#ccebd8}.btn.telegram{background:#174d70;border-color:#2b78a8;color:#e4f4ff}.btn.generate{background:#3a2b12;border-color:#8b6b31;color:#f4ddb1}.btn[disabled]{opacity:.45;cursor:not-allowed}
.status{padding:8px 11px;border-radius:999px;border:1px solid var(--line);font-size:12px;text-transform:uppercase;letter-spacing:.08em;white-space:nowrap}.status.approved{color:#bfe8ce;border-color:#2f6d4d;background:#10291d}.status.draft{color:#ecd3a1;border-color:#6c542f;background:#2a2113}.status.published{color:#b9ddf5;border-color:#2b78a8;background:#102a3b}.status.publishing{color:#f0d39d;border-color:#82672d;background:#2d2512}.status.partially_published{color:#f1c18b;border-color:#8a5829;background:#35200f}.status.failed{color:#f0b1b1;border-color:#884444;background:#341616}
.wrap{max-width:1500px;margin:0 auto;padding:24px}.empty{max-width:760px;margin:80px auto;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:32px;text-align:center}.empty h2,.result-box h1{font-family:Georgia,serif;color:var(--gold)}.notice{max-width:1500px;margin:0 auto 14px;padding:13px 16px;border:1px solid #6c542f;border-radius:12px;background:#2a2113;color:#ecd3a1}.notice.error{border-color:#884444;background:#341616;color:#f1b6b6}.notice.info{border-color:#315c77;background:#102738;color:#c6e4f5}.notice ul{margin:7px 0 0;padding-left:22px}.quality-grid{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}.quality-chip{border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:11px;color:var(--muted)}.diversity-panel{border:1px solid #315c77;background:#0d2232;border-radius:14px;padding:16px;margin:0 0 14px}.diversity-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.diversity-head b{font-family:Georgia,serif;color:var(--gold);font-size:18px}.diversity-form{display:grid;grid-template-columns:repeat(3,minmax(150px,220px)) auto auto;gap:10px;align-items:end;margin-top:12px}.diversity-form label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}.diversity-form select,.diversity-form input{width:100%;margin-top:5px;background:#071522;color:var(--ivory);border:1px solid var(--line);border-radius:8px;padding:9px}.threshold-presets{display:flex;gap:7px;flex-wrap:wrap;margin-top:8px}.threshold-presets .btn{padding:7px 10px;font-size:11px}.conflict-list{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:10px}.conflict-item{border:1px solid var(--line);border-radius:10px;padding:12px;background:#091a28}.conflict-item.error{border-color:#884444}.conflict-item.warning{border-color:#6c542f}.conflict-meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px}.conflict-texts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:9px}.conflict-copy{border:1px solid var(--line);border-radius:8px;padding:9px;background:#071522;color:#cfd9e2;font-size:12px;line-height:1.45}.conflict-copy b{display:block;color:var(--gold);margin-bottom:4px}.regen-feedback{border:1px solid #2b6749;background:#10291d;border-radius:12px;padding:13px 16px;margin-bottom:14px}.model-note{font-size:11px;color:var(--muted);white-space:nowrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:22px}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;overflow:hidden}.card-head{padding:15px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px}.card-head .grow{flex:1}.mini-btn{font-size:11px;padding:7px 9px}.glyph{font-size:30px;color:var(--gold)}.name{font-family:Georgia,serif;font-size:20px;letter-spacing:.08em}.layout{display:grid;grid-template-columns:190px 1fr;gap:16px;padding:16px}.preview{width:190px;border-radius:10px;border:1px solid #334b5e;align-self:start}.field-head{display:flex;align-items:center;gap:8px;margin:0 0 5px}.field-head label{display:block;flex:1;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0}.field-regen{font-size:10px;padding:5px 7px}.fields textarea{width:100%;resize:vertical;background:#071522;color:var(--ivory);border:1px solid var(--line);border-radius:9px;padding:10px 11px;font:14px/1.4 Arial,sans-serif;margin-bottom:10px}.fields textarea.card-copy{min-height:72px}.fields textarea.long{min-height:92px}.fields textarea:disabled{opacity:.62}
.footer{position:sticky;bottom:0;background:#071522ee;backdrop-filter:blur(14px);border-top:1px solid var(--line);padding:14px 24px;display:flex;justify-content:flex-end;gap:12px;flex-wrap:wrap}.hint{color:var(--muted);font-size:12px;line-height:1.5}.history-row{display:grid;grid-template-columns:150px 80px 130px 150px 180px 1fr;gap:14px;align-items:center;padding:14px 16px;border:1px solid var(--line);border-radius:11px;margin:9px 0;background:var(--panel)}
a{color:#e3c58f}.approved-text{color:#8ad0a5}.draft-text{color:#dfb567}.published-text{color:#8fc8ed}.result-box{max-width:1050px;margin:40px auto;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:28px}pre{white-space:pre-wrap;background:#071522;border:1px solid #294055;padding:18px;border-radius:12px;max-height:560px;overflow:auto}
@media(max-width:900px){.conflict-texts{grid-template-columns:1fr}.diversity-form{grid-template-columns:1fr 1fr}.diversity-form .wide{grid-column:1/-1}}@media(max-width:700px){.layout{grid-template-columns:1fr}.preview{width:100%;max-width:330px;margin:auto}.grid{grid-template-columns:1fr}.top,.footer{position:static}.history-row{grid-template-columns:1fr 1fr}.history-row .updated{grid-column:1/-1}}
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def page(title: str, body: str) -> bytes:
    doc = f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>'
    return doc.encode("utf-8")


def status_label(status_state: str, publication_state: str | None) -> tuple[str, str]:
    if publication_state in {"publishing", "partially_published", "published", "failed"}:
        labels = {
            "publishing": "PUBLISHING",
            "partially_published": "PARTIALLY PUBLISHED",
            "published": "PUBLISHED",
            "failed": "FAILED",
        }
        return publication_state, labels[publication_state]
    return ("approved", "APPROVED") if status_state == "approved" else ("draft", "DRAFT")


def _issue_target(issue: dict) -> str | None:
    sign = str(issue.get("sign") or "")
    field = str(issue.get("field") or "")
    if sign in SIGN_ORDER and field:
        return f"field-{sign}-{field}"
    return None


def validation_html(issues: list[dict]) -> str:
    # Рекомендованная длина карточки — внутренняя диагностическая метрика, а не
    # пользовательская проблема Review Console. Реальные min/max ошибки остаются видимыми.
    visible = [x for x in issues if x.get("code") != "card_target_length"]
    if not visible:
        return ""
    errors = [x for x in visible if x.get("severity") == "error"]
    warnings = [x for x in visible if x.get("severity") == "warning"]

    def render_item(issue: dict) -> str:
        target = _issue_target(issue)
        jump = f' <a href="#{esc(target)}">Перейти →</a>' if target else ""
        return (
            f'<li>{esc(issue.get("sign") or "dataset")} · {esc(issue.get("field") or "—")}: '
            f'{esc(issue["message"])}{jump}</li>'
        )

    blocks = []
    if errors:
        items = "".join(render_item(x) for x in errors)
        blocks.append(f'<div class="notice error"><b>Validation errors · {len(errors)}</b><ul>{items}</ul></div>')
    if warnings:
        items = "".join(render_item(x) for x in warnings)
        blocks.append(f'<div class="notice"><b>Quality warnings · {len(warnings)}</b><ul>{items}</ul></div>')
    return "".join(blocks)


def regeneration_feedback_html(payload: dict) -> str:
    uniqueness = ((payload.get("generation") or {}).get("uniqueness") or {})
    feedback = uniqueness.get("last_regeneration")
    if not isinstance(feedback, dict):
        return ""
    kind = str(feedback.get("kind") or "field")
    sign = str(feedback.get("sign") or "")
    field = feedback.get("field")
    attempts = int(feedback.get("attempts") or 0)
    before = float(feedback.get("before_similarity") or 0.0)
    after = float(feedback.get("after_similarity") or 0.0)
    resolved = bool(feedback.get("resolved"))
    result = "Конфликт устранён" if resolved else "Конфликт всё ещё требует внимания"
    if kind == "conflicts":
        changed = [str(item) for item in (feedback.get("changed") or [])]
        target_label = f"Conflicts · {', '.join(changed)}" if changed else "Conflicts"
    else:
        target = f" · {field}" if field else " · весь прогноз"
        target_label = f"{sign.title()}{target}"
    return (
        '<div class="regen-feedback"><b>Последняя regeneration</b><div class="quality-grid">'
        f'<span class="quality-chip">{esc(target_label)}</span>'
        f'<span class="quality-chip">Попыток: {attempts}</span>'
        f'<span class="quality-chip">Было: {before:.0%}</span>'
        f'<span class="quality-chip">Стало: {after:.0%}</span>'
        f'<span class="quality-chip">{esc(result)}</span>'
        '</div></div>'
    )


def diversity_controls_html(
    settings: DiversitySettings,
    *,
    conflict_count: int,
    locked: bool,
    regenerate_disabled: bool,
    conflicts: list[dict] | None = None,
    day: str = "",
    language: str = "ru",
) -> str:
    rules = settings.to_rules()
    profile_options = "".join(
        f'<option value="{esc(key)}"{" selected" if settings.profile == key else ""}>{esc(label)}</option>'
        for key, label in PROFILE_LABELS.items()
    )
    history_options = "".join(
        f'<option value="{days}"{" selected" if settings.history_days == days else ""}>{days} дней</option>'
        for days in HISTORY_OPTIONS
    )
    custom_percent = int(round(settings.custom_threshold * 100))
    disabled = " disabled" if locked else ""
    regen_disabled = " disabled" if regenerate_disabled or conflict_count <= 0 else ""
    preset_buttons = "".join(
        f'<button class="btn" form="reviewForm" type="submit" formaction="/diversity-settings" name="threshold_preset" value="{percent}"{disabled}>{percent}%</button>'
        for percent in (70, 80, 90, 95)
    )

    conflict_rows = []
    for issue in (conflicts or [])[:24]:
        similarity = float(issue.get("similarity") or 0.0)
        severity = str(issue.get("severity") or "warning")
        sign = str(issue.get("sign") or "dataset")
        field = str(issue.get("field") or "—")
        matched_date = str(issue.get("matched_date") or "текущий день")
        matched_sign = str(issue.get("matched_sign") or sign)
        current_text = str(issue.get("current_text") or "")
        matched_text = str(issue.get("matched_text") or "")
        target = f"{sign}__{field}"
        field_regen_disabled = " disabled" if regenerate_disabled else ""
        regen_button = ""
        if sign in SIGN_ORDER and field not in {"", "—"}:
            regen_button = (
                f'<button class="btn field-regen" form="reviewForm" type="submit" formaction="/regenerate-field" '
                f'name="target" value="{esc(target)}"{field_regen_disabled}>Regenerate this field</button>'
            )
        current_block = f'<div class="conflict-copy"><b>Current</b>{esc(current_text)}</div>' if current_text else ""
        matched_block = (
            f'<div class="conflict-copy"><b>Match · {esc(matched_date)} · {esc(matched_sign)}</b>{esc(matched_text)}</div>'
            if matched_text else ""
        )
        conflict_rows.append(
            f'<li class="conflict-item {esc(severity)}">'
            f'<div class="conflict-meta"><b>{esc(sign)} · {esc(field)}</b>'
            f'<span class="quality-chip">{similarity:.0%}</span>'
            f'<span class="quality-chip">{esc(severity.upper())}</span>'
            f'<span class="quality-chip">{esc(matched_date)}</span>{regen_button}</div>'
            f'<div class="conflict-texts">{current_block}{matched_block}</div></li>'
        )
    conflict_html = (
        f'<ul class="conflict-list">{"".join(conflict_rows)}</ul>'
        if conflict_rows
        else '<div class="hint" style="margin-top:10px">Конфликтов и предупреждений по текущему уровню не найдено.</div>'
    )
    return (
        '<section class="diversity-panel">'
        '<div class="diversity-head"><b>Content Diversity</b>'
        f'<span class="quality-chip">Профиль: {esc(settings.label)}</span>'
        f'<span class="quality-chip">История: {settings.history_days} дней</span>'
        f'<span class="quality-chip">Hard threshold: {rules.hard_threshold:.0%}</span>'
        f'<span class="quality-chip">Hard conflicts: {conflict_count}</span></div>'
        '<div class="hint" style="margin-top:8px">Смена threshold и «Проверить без API» выполняют только локальный анализ. Токены расходуются только после Regenerate.</div>'
        f'<div class="threshold-presets"><span class="hint">Быстро:</span>{preset_buttons}</div>'
        '<div class="diversity-form">'
        f'<label>Профиль<select form="reviewForm" name="profile"{disabled}>{profile_options}</select></label>'
        f'<label>История<select form="reviewForm" name="history_days"{disabled}>{history_options}</select></label>'
        f'<label>Custom threshold, %<input form="reviewForm" type="number" name="custom_threshold" min="70" max="95" step="1" value="{custom_percent}"{disabled}></label>'
        f'<button class="btn wide" form="reviewForm" type="submit" formaction="/diversity-settings"{disabled}>Проверить без API</button>'
        f"<button class=\"btn generate wide\" form=\"reviewForm\" type=\"submit\" formaction=\"/regenerate-conflicts\"{regen_disabled} onclick=\"return confirm('Перегенерировать только найденные hard conflicts? API будет вызван только для этих полей.');\">Regenerate conflicts · {conflict_count}</button>"
        '</div>'
        f'{conflict_html}</section>'
    )


def automation_status_html(day: str, language: str) -> str:
    state = derive_state(day, language)
    label = state.state.replace("_", " ").upper()
    details = [f"Состояние: {label}"]
    if state.last_operation:
        details.append(f"Последняя операция: {state.last_operation} / {state.last_result or '—'}")
    if state.attempt:
        details.append(f"Попытка: {state.attempt}")
    if state.updated_at:
        details.append(f"Обновлено: {state.updated_at}")
    error = state.last_error or {}
    error_html = ""
    if error:
        error_html = f'<div class="hint" style="margin-top:7px">{esc(error.get("type") or "Error")}: {esc(error.get("message") or "")}</div>'
    chips = "".join(f'<span class="quality-chip">{esc(item)}</span>' for item in details)
    return f'<section class="notice info"><b>Automation</b><div class="quality-grid">{chips}</div>{error_html}</section>'


def review_html(day: str, language: str) -> bytes:
    base = day_dir(day, language)
    payload = load_json(base / "content.json")
    status = load_json(base / "status.json", {"state": "draft"}) or {"state": "draft"}
    state = status.get("state", "draft")
    publication = load_json(base / "publication.json", {}) or {}
    publication_state = publication.get("state")
    ui_class, ui_label = status_label(state, publication_state)
    issues = validation_issues(day, language) if payload else []
    locked = publication_locks_editing(day, language) if payload else False
    settings = diversity_settings() if payload else None
    rules = settings.to_rules() if settings else None
    automation_state = derive_state(day, language)
    automation_panel = automation_status_html(day, language)

    target_tomorrow = tomorrow_iso()
    tomorrow_exists = content_path(target_tomorrow, language).exists()
    ai_config = None if tomorrow_exists else openai_config_message()
    if tomorrow_exists:
        tomorrow_control = f'<a class="btn generate" href="/review?date={quote(target_tomorrow)}&language={quote(language)}">Открыть завтра · {esc(target_tomorrow)}</a>'
    else:
        tomorrow_control = (
            '<form method="post" action="/generate-tomorrow" '
            'onsubmit="if(!confirm(\'Сгенерировать прогноз на завтра через AI provider?\'))return false;var b=this.querySelector(\'button\');b.disabled=true;b.textContent=\'Генерация…\';">'
            f'<input type="hidden" name="language" value="{esc(language)}">'
            f'<button class="btn generate" type="submit"{" disabled" if ai_config else ""}>Generate tomorrow · {esc(target_tomorrow)}</button></form>'
        )

    top = f"""
<header class="top"><div class="brand">CAELUS</div>
<form method="get" action="/review" class="controls"><input type="date" name="date" value="{esc(day)}"><select name="language"><option value="ru" {'selected' if language=='ru' else ''}>Русский</option><option value="en" {'selected' if language=='en' else ''}>English</option></select><button class="btn" type="submit">Открыть</button></form>
<a class="btn" href="/history">История</a>{tomorrow_control}<span class="model-note">AI: {esc(openai_model())} · reasoning: {esc(openai_reasoning_effort())}</span><div class="spacer"></div>{f'<span class="status {esc(ui_class)}">{esc(ui_label)}</span>' if payload else ''}</header>"""

    if not payload:
        ai_notice = f'<div class="notice info" style="text-align:left">{esc(ai_config)}</div>' if ai_config else ""
        body = f"""{top}<main class="wrap">{automation_panel}{ai_notice}<section class="empty"><h2>Контента на {esc(day)} ещё нет</h2><p class="hint">Основной ежедневный workflow запускается кнопкой Generate tomorrow. Для разработки можно создать mock-комплект без API.</p><div class="actions">
<form method="post" action="/generate"><input type="hidden" name="date" value="{esc(day)}"><input type="hidden" name="language" value="{esc(language)}"><input type="hidden" name="mode" value="mock"><button class="btn" type="submit">Создать mock-комплект</button></form>
<form method="post" action="/generate"><input type="hidden" name="date" value="{esc(day)}"><input type="hidden" name="language" value="{esc(language)}"><input type="hidden" name="mode" value="ai"><button class="btn primary" type="submit"{" disabled" if openai_config_message() else ""}>{"Retry AI generation" if automation_state.state == "generation_failed" else "Сгенерировать AI"}</button></form>
</div></section></main>"""
        return page("CAELUS · Review", body)

    notices = validation_html(issues)
    uniqueness_issues = [x for x in issues if x.get("code") in {"history_repetition", "history_similarity_warning", "same_day_repetition"}]
    if settings and rules:
        hard_conflicts = [
            x for x in uniqueness_issues
            if x.get("severity") == "error"
        ]
        generation_provider = str((payload.get("generation") or {}).get("provider") or "openai").lower()
        regen_unavailable = generation_provider != "mock" and bool(openai_config_message())
        notices += diversity_controls_html(
            settings,
            conflict_count=len(hard_conflicts),
            locked=locked,
            regenerate_disabled=locked or regen_unavailable,
            conflicts=uniqueness_issues,
            day=day,
            language=language,
        )
        notices += regeneration_feedback_html(payload)
    if rules:
        historical = [x for x in uniqueness_issues if x.get("code", "").startswith("history_")]
        max_similarity = max((float(x.get("similarity") or 0.0) for x in historical), default=0.0)
        hist_errors = sum(1 for x in historical if x.get("severity") == "error")
        hist_warnings = sum(1 for x in historical if x.get("severity") == "warning")
        verdict = "✓ критических исторических повторов нет" if hist_errors == 0 else f"⚠ найдено критических повторов: {hist_errors}"
        notices += (
            '<div class="notice info"><b>Content uniqueness</b><div class="quality-grid">'
            f'<span class="quality-chip">История: {rules.history_days} дней</span>'
            f'<span class="quality-chip">Автоисправление: ≥ {rules.hard_threshold:.0%}</span>'
            f'<span class="quality-chip">{esc(verdict)}</span>'
            f'<span class="quality-chip">Предупреждения: {hist_warnings}</span>'
            f'<span class="quality-chip">Макс. сходство: {max_similarity:.0%}</span>'
            '</div></div>'
        )
    generation = payload.get("generation") or {}
    usage = generation.get("api_usage") if isinstance(generation, dict) else None
    if isinstance(usage, dict) and int(usage.get("request_count") or 0):
        monthly = monthly_api_usage(day)
        dataset_cost = usage.get("estimated_cost_usd")
        month_cost = monthly.get("estimated_cost_usd")
        cost_text = f"${float(dataset_cost):.6f}" if dataset_cost is not None else "н/д для этой модели"
        month_cost_text = f"${float(month_cost):.4f}" if month_cost is not None else "н/д"
        unpriced = int(monthly.get("unpriced_datasets") or 0)
        if unpriced:
            month_cost_text += f" + {unpriced} dataset без тарифа"
        notices += (
            '<div class="notice info"><b>OpenAI API usage</b><div class="quality-grid">'
            f'<span class="quality-chip">Запросов: {int(usage.get("request_count") or 0)}</span>'
            f'<span class="quality-chip">Input: {int(usage.get("prompt_tokens") or 0):,}</span>'
            f'<span class="quality-chip">Cached: {int(usage.get("cached_prompt_tokens") or 0):,}</span>'
            f'<span class="quality-chip">Output: {int(usage.get("completion_tokens") or 0):,}</span>'
            f'<span class="quality-chip">Reasoning: {int(usage.get("reasoning_tokens") or 0):,}</span>'
            f'<span class="quality-chip">Total: {int(usage.get("total_tokens") or 0):,}</span>'
            f'<span class="quality-chip">Этот dataset ≈ {esc(cost_text)}</span>'
            f'<span class="quality-chip">{esc(str(monthly.get("month") or day[:7]))}: ≈ {esc(month_cost_text)}</span>'
            '</div><div class="hint" style="margin-top:8px">Токены взяты из фактического usage ответа API. Стоимость — расчёт по сохранённому тарифу модели на момент версии CAELUS; reasoning входит в Output и отдельно второй раз не оплачивается.</div></div>'
        )
    if has_errors(issues):
        error_count = sum(1 for issue in issues if issue.get("severity") == "error")
        notices += (
            f'<div class="notice error"><b>Approve All заблокирован.</b> Осталось ошибок: {error_count}. '
            'Используйте ссылки «Перейти →» или Regenerate в Content Diversity.</div>'
        )
    if ai_config:
        notices += f'<div class="notice info"><b>AI configuration</b><br>{esc(ai_config)}</div>'
    tg_config = telegram_config_message() if state == "approved" and publication_state != "published" else None
    if tg_config:
        notices += f'<div class="notice info"><b>Telegram configuration</b><br>{esc(tg_config)}</div>'
    if locked:
        notices += '<div class="notice info"><b>Редактирование заблокировано.</b> Публикация этого approved-комплекта уже началась; завершите или продолжите её, чтобы не нарушить content_hash и progress journal.</div>'

    cards = []
    disabled = " disabled" if locked else ""
    for sign in SIGN_ORDER:
        item = payload["signs"][sign]
        p = base / "cards" / f"caelus_{sign}.png"
        version = int(p.stat().st_mtime) if p.exists() else 0
        image = f"/generated/{quote(day)}/{quote(language)}/cards/caelus_{quote(sign)}.png?v={version}"
        specs = [
            ("card", "Карточка / short", "card-copy"),
            ("general", "Telegram · General", "long"),
            ("love", "Love / отношения", ""),
            ("career_money", "Career & money", ""),
            ("advice", "Advice / совет дня", ""),
        ]
        generation_provider = str((payload.get("generation") or {}).get("provider") or "openai").lower()
        regen_unavailable = generation_provider != "mock" and bool(openai_config_message())
        regen_disabled = " disabled" if (locked or regen_unavailable) else ""
        fields = []
        for field, label, cls in specs:
            maxlength = ' maxlength="220"' if field == "card" else ""
            field_target = f"{sign}__{field}"
            field_button = (
                f'<button class="btn field-regen" type="submit" form="reviewForm" formaction="/regenerate-field" name="target" value="{esc(field_target)}"'
                f'{regen_disabled} onclick="return confirm(\'Перегенерировать только {esc(label)} для {esc(item["name"])}?\');">Regenerate</button>'
            )
            fields.append(
                f'<div id="field-{esc(sign)}-{esc(field)}"><div class="field-head"><label>{esc(label)}</label>{field_button}</div>'
                f'<textarea class="{cls}" name="{sign}__{field}"{maxlength}{disabled}>{esc(get_field(item, field))}</textarea></div>'
            )
        regen_button = (
            f'<button class="btn mini-btn" type="submit" form="reviewForm" formaction="/regenerate-sign" name="sign" value="{esc(sign)}"'
            f'{regen_disabled} onclick="return confirm(\'Полностью перегенерировать {esc(item["name"])}? Остальные введённые правки будут сначала сохранены.\');">Regenerate sign</button>'
        )
        cards.append(f'<article class="card"><div class="card-head"><div class="glyph">{GLYPHS[sign]}</div><div class="name">{esc(item["name"])}</div><div class="grow"></div>{regen_button}</div><div class="layout"><img class="preview" src="{image}" alt="{esc(item["name"])}"><div class="fields">{"".join(fields)}</div></div></article>')

    footer = []
    if state == "approved" and not locked:
        footer.append(f'<form method="post" action="/return-to-draft" onsubmit="return confirm(\'Вернуть approved-комплект в DRAFT?\');"><input type="hidden" name="date" value="{esc(day)}"><input type="hidden" name="language" value="{esc(language)}"><button class="btn" type="submit">Вернуть в draft</button></form>')
    if not locked:
        footer.append('<button class="btn" type="submit" form="reviewForm" formaction="/save">Сохранить и пересобрать</button>')
        footer.append(f'<button class="btn ok" type="submit" form="reviewForm" formaction="/approve"{" disabled" if has_errors(issues) else ""}>Approve All</button>')

    if state == "approved":
        if publication_state == "published":
            footer.append('<button class="btn telegram" type="button" disabled>Telegram · опубликовано</button>')
        else:
            label = "Продолжить публикацию" if publication_state in {"publishing", "partially_published", "failed"} else "Publish Telegram"
            cfg_disabled = " disabled" if (tg_config or has_errors(issues)) else ""
            footer.append(
                '<form method="post" action="/publish-telegram" onsubmit="return confirm(\'Опубликовать approved-комплект в Telegram?\');">'
                                f'<button class="btn telegram" type="submit"{cfg_disabled}>{esc(label)}</button></form>'
            )

    body = f'{top}<main class="wrap">{automation_panel}{notices}<form id="reviewForm" method="post"><input type="hidden" name="date" value="{esc(day)}"><input type="hidden" name="language" value="{esc(language)}"><div class="grid">{"".join(cards)}</div></form></main><footer class="footer">{"".join(footer)}</footer>'
    return page("CAELUS · Review", body)


def publish_result_html(day: str, language: str, returncode: int, output: str) -> bytes:
    publication = load_json(publication_path(day, language), {}) or {}
    state = publication.get("state", "published" if returncode == 0 else "failed")
    labels = {"published": "PUBLISHED", "partially_published": "PARTIALLY PUBLISHED", "failed": "FAILED", "publishing": "PUBLISHING"}
    title = "Telegram · публикация завершена" if state == "published" else "Telegram · публикация не завершена"
    back = f"/review?date={quote(day)}&language={quote(language)}"
    body = (
        f'<main class="wrap"><section class="result-box"><span class="status {esc(state)}">{esc(labels.get(state, state.upper()))}</span>'
        f'<h1>{esc(title)}</h1><p class="hint">Секреты Telegram здесь не отображаются. Progress сохранён в publication.json; при частичном сбое повторный запуск продолжит с сохранённого шага.</p>'
        f'<pre>{esc(output or "Publisher не вернул текстовый вывод.")}</pre><div class="actions"><a class="btn primary" href="{back}">← Вернуться в Review Console</a></div></section></main>'
    )
    return page(title, body)


def history_html() -> bytes:
    targets: set[tuple[str, str]] = set(iter_automation_targets())
    if _generated_root().exists():
        for date_folder in _generated_root().iterdir():
            if not date_folder.is_dir() or date_folder.name.startswith("_"):
                continue
            for lang_folder in date_folder.iterdir():
                if lang_folder.is_dir() and (lang_folder / "content.json").is_file():
                    targets.add((date_folder.name, lang_folder.name))

    rows = []
    for day, language in sorted(targets, reverse=True):
        try:
            auto = derive_state(day, language)
        except ValueError:
            continue
        base = _generated_root() / day / language
        status = load_json(base / "status.json", {"state": "draft"}) or {"state": "draft"}
        publication = load_json(base / "publication.json", {}) or {}
        review_state = status.get("state", "—") if (base / "content.json").is_file() else "—"
        pub_state = publication.get("state") or "—"
        last_run = f"{auto.last_operation} / {auto.last_result} / {auto.attempt}" if auto.last_operation else "—"
        updated = auto.updated_at or publication.get("published_at") or publication.get("failed_at") or status.get("approved_at") or status.get("updated_at") or "—"
        rows.append(
            f'<div class="history-row"><a href="/review?date={quote(day)}&language={quote(language)}">{esc(day)}</a>'
            f'<div>{esc(language.upper())}</div><div>{esc(str(review_state).upper())}</div>'
            f'<div>{esc(str(pub_state).replace("_", " ").upper())}</div>'
            f'<div>{esc(auto.state.replace("_", " ").upper())}</div>'
            f'<div class="updated">{esc(last_run)} · {esc(updated)}</div></div>'
        )
    content = "".join(rows) or "<p>Пока нет сгенерированных комплектов.</p>"
    return page("CAELUS · History", f'<main class="wrap"><a href="/">← Review</a><h1 class="brand" style="margin:24px 0">CAELUS · HISTORY</h1><div class="hint">DATE · LANG · REVIEW · PUBLICATION · AUTOMATION STATE · LAST RUN</div>{content}</main>')


class Handler(BaseHTTPRequestHandler):
    server_version = "CAELUSReview/1.11"

    def log_message(self, fmt, *args):
        print("[HTTP]", fmt % args)

    def send_bytes(self, data: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def error_page(self, title: str, message: str, status: int = 500):
        body = f'<main class="wrap"><h1 style="color:#d5aa66">{esc(title)}</h1><pre>{esc(message)}</pre><a href="javascript:history.back()">← Назад</a></main>'
        self.send_bytes(page(title, body), status=status)

    def read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2 * 1024 * 1024:
            raise ValueError("Form is too large")
        body = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(body, keep_blank_values=True)
        return {k: v[-1] for k, v in parsed.items()}

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/healthz":
                data = json.dumps(health_payload(), ensure_ascii=False).encode("utf-8")
                self.send_bytes(data, "application/json; charset=utf-8", status=200)
                return
            if parsed.path == "/":
                self.redirect(f"/review?date={date.today().isoformat()}&language=ru")
                return
            if parsed.path == "/review":
                q = parse_qs(parsed.query)
                day = q.get("date", [date.today().isoformat()])[-1]
                language = q.get("language", ["ru"])[-1]
                self.send_bytes(review_html(day, language))
                return
            if parsed.path == "/history":
                self.send_bytes(history_html())
                return
            if parsed.path.startswith("/generated/"):
                rel = parsed.path[len("/generated/"):]
                target = (_generated_root() / rel).resolve()
                root = _generated_root().resolve()
                if root not in target.parents or not target.is_file():
                    self.error_page("404", "Файл не найден", 404)
                    return
                data = target.read_bytes()
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self.send_bytes(data, ctype)
                return
            self.error_page("404", "Страница не найдена", 404)
        except Exception as exc:
            self.error_page("Ошибка", str(exc), 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            form = self.read_form()
            language = form.get("language", "ru")

            if parsed.path == "/diversity-settings":
                day = form.get("date", date.today().isoformat())
                preset = form.get("threshold_preset")
                custom_raw = float(preset or form.get("custom_threshold", "80")) / 100.0
                profile = "custom" if preset else form.get("profile", "strict")
                if content_path(day, language).exists():
                    save_fields(day, language, form)
                save_diversity_settings(
                    profile=profile,
                    history_days=int(form.get("history_days", "90")),
                    custom_threshold=custom_raw,
                )
                # Только локальная preview-проверка: provider здесь не создаётся.
                if content_path(day, language).exists():
                    preview_diversity(day, language)
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return

            if parsed.path == "/regenerate-conflicts":
                day = form.get("date", date.today().isoformat())
                preset = form.get("threshold_preset")
                custom_raw = float(preset or form.get("custom_threshold", "80")) / 100.0
                profile = "custom" if preset else form.get("profile", "strict")
                if content_path(day, language).exists():
                    save_fields(day, language, form)
                save_diversity_settings(
                    profile=profile,
                    history_days=int(form.get("history_days", "90")),
                    custom_threshold=custom_raw,
                )
                regenerate_conflicts(day, language)
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return

            if parsed.path == "/generate-tomorrow":
                target_day = tomorrow_iso()
                day_dir(target_day, language)
                run_generation_job(target_day, language, mode="ai")
                self.redirect(f"/review?date={quote(target_day)}&language={quote(language)}")
                return

            day = form.get("date", date.today().isoformat())
            day_dir(day, language)
            if parsed.path == "/generate":
                run_generation_job(day, language, mode=form.get("mode", "mock"))
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return
            if parsed.path == "/save":
                save_fields(day, language, form)
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return
            if parsed.path == "/regenerate-field":
                target = form.get("target", "")
                if "__" not in target:
                    raise ValueError("Некорректная цель точечной перегенерации")
                sign, field = target.split("__", 1)
                # Сначала сохраняем все ручные правки из формы, чтобы действие не потеряло их.
                save_fields(day, language, form)
                regenerate_field(day, language, sign, field)
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return
            if parsed.path == "/regenerate-sign":
                sign = form.get("sign", "")
                # Сохраняем остальные введённые правки, чтобы нажатие Regenerate не потеряло их.
                save_fields(day, language, form)
                regenerate_sign(day, language, sign)
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return
            if parsed.path == "/approve":
                approve(day, language, form)
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return
            if parsed.path == "/return-to-draft":
                return_to_draft(day, language)
                self.redirect(f"/review?date={quote(day)}&language={quote(language)}")
                return
            if parsed.path == "/publish-telegram":
                automation_state = run_publication_job(day, language)
                output = (
                    f"Automation result: {automation_state.last_result}\n"
                    f"Effective state: {automation_state.state}\n"
                    f"Attempt: {automation_state.attempt}"
                )
                self.send_bytes(publish_result_html(day, language, 0, output), status=200)
                return
            self.error_page("404", "Страница не найдена", 404)
        except subprocess.CalledProcessError as exc:
            self.error_page("Ошибка выполнения", f"Команда завершилась с кодом {exc.returncode}: {exc.cmd}", 500)
        except Exception as exc:
            self.error_page("Ошибка", str(exc), 500)


def main() -> None:
    load_dotenv()
    host = admin_host()
    port = admin_port()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"CAELUS Review Console: http://{host}:{port}")
    if not (ROOT / ".env").exists():
        print("[INFO] .env не найден. Это нормально для ZIP; создайте локальный .env для AI/Telegram.")
    print("Для остановки нажмите Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
