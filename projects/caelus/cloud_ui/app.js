const ZODIAC = ["aries","taurus","gemini","cancer","leo","virgo","libra","scorpio","sagittarius","capricorn","aquarius","pisces"];


const EDITABLE_FIELDS = ["card","overview","relationships","work_money","advice"];

const CANONICAL_FIELD_BY_REVIEW_FIELD = {
  card: "card",
  overview: "general",
  relationships: "love",
  work_money: "career_money",
  advice: "advice",
};

export function canonicalFieldName(field) {
  return CANONICAL_FIELD_BY_REVIEW_FIELD[field] ?? field;
}

export function reviewFieldValue(source = {}, field) {
  const canonical = canonicalFieldName(field);
  return source?.[canonical] ?? source?.[field] ?? "";
}

export function aucklandDate(value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Pacific/Auckland", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(value);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

export function artifactBySign(artifacts = []) {
  const result = {};
  for (const artifact of artifacts) {
    if (artifact?.mime_type !== "image/png") continue;
    const match = String(artifact?.logical_name ?? "").toLowerCase().match(/_([a-z]+)\.png$/);
    if (match && ZODIAC.includes(match[1])) result[match[1]] = artifact;
  }
  return result;
}

export function uniquenessDiagnostics(payload = {}) {
  return payload?.generation?.uniqueness ?? payload?.uniqueness ?? payload?.validation?.uniqueness ?? {};
}

function stableKey(operation, input) {
  return `${operation}:${input.contentKey}:${input.version}:${JSON.stringify(input.payload ?? {})}`;
}

export class StaleVersionError extends Error {
  constructor(message = "Content changed; reload current revision before retrying.") {
    super(message);
    this.name = "StaleVersionError";
  }
}

export function createCommandCoordinator({
  projectId = "caelus",
  origin = globalThis.location?.origin ?? "",
  fetchImpl = globalThis.fetch,
  uuid = () => crypto.randomUUID(),
  onStale = () => {},
  onFailure = () => {},
} = {}) {
  const inFlight = new Map();
  return {
    post(input) {
      const key = input.actionKey || `${input.operation}:${input.contentKey}:${input.expectedVersion}`;
      const existing = inFlight.get(key);
      if (existing) return existing;
      const executionId = uuid();
      const idempotencyKey = uuid();
      const request = fetchImpl(`${origin}/api/projects/${encodeURIComponent(projectId)}/commands`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          execution_id: executionId,
          operation: input.operation,
          content_key: input.contentKey,
          content_set_id: input.contentSetId ?? null,
          expected_version: input.expectedVersion,
          payload: input.payload ?? {},
          idempotency_key: idempotencyKey,
        }),
      }).then(async (response) => {
        if (response.status === 409) {
          const error = new StaleVersionError();
          onStale(error.message);
          throw error;
        }
        if (!response.ok) {
          const detail = await response.text();
          onFailure(`Command failed: HTTP ${response.status}`);
          throw new Error(detail || `HTTP ${response.status}`);
        }
        return response.json();
      }).finally(() => inFlight.delete(key));
      inFlight.set(key, request);
      return request;
    },
  };
}

export class CommandClient {
  constructor({ projectId, origin = globalThis.location?.origin ?? "", fetcher = globalThis.fetch, onMessage = () => {}, uuid = () => crypto.randomUUID() }) {
    this.coordinator = createCommandCoordinator({
      projectId, origin, fetchImpl: fetcher, uuid,
      onStale: onMessage, onFailure: onMessage,
    });
  }

  submit(operation, input) {
    return this.coordinator.post({
      actionKey: stableKey(operation, input),
      operation,
      contentKey: input.contentKey,
      contentSetId: input.contentSetId,
      expectedVersion: input.version,
      payload: input.payload ?? {},
    });
  }
}

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function contentKey() {
  return `${document.querySelector("#date").value}:${document.querySelector("#language").value}`;
}

function readFields() {
  const signs = {};
  for (const card of document.querySelectorAll("[data-sign]")) {
    const sign = card.dataset.sign;
    signs[sign] = {};
    for (const input of card.querySelectorAll("[data-field]")) signs[sign][canonicalFieldName(input.dataset.field)] = input.value;
  }
  return { signs };
}

function renderCards(payload, artifacts = []) {
  const root = document.querySelector("#cards");
  root.replaceChildren();
  const signs = payload?.signs ?? {};
  const previews = artifactBySign(artifacts);
  for (const sign of ZODIAC) {
    const source = signs[sign] ?? {};
    const article = document.createElement("article");
    article.className = "card";
    article.dataset.sign = sign;
    const artifact = artifactBySign(artifacts)[sign];
    if (artifact?.artifact_id) {
      const image = document.createElement("img");
      image.className = "card-preview";
      image.alt = `${source.name || sign} preview`;
      image.src = `/api/projects/caelus/artifacts/${encodeURIComponent(artifact.artifact_id)}`;
      article.append(image);
    }
    const title = document.createElement("h2");
    title.textContent = source.name || sign.toUpperCase();
    article.append(title);
    for (const field of EDITABLE_FIELDS) {
      const wrap = document.createElement("label"); wrap.className = "field";
      const head = document.createElement("span"); head.className = "field-head";
      const caption = document.createElement("span"); caption.textContent = field;
      const fieldButton = document.createElement("button");
      fieldButton.type = "button"; fieldButton.className = "secondary"; fieldButton.textContent = "Regenerate Field";
      fieldButton.dataset.regenerateField = field; fieldButton.dataset.sign = sign;
      head.append(caption, fieldButton);
      const textarea = document.createElement("textarea"); textarea.dataset.field = field; textarea.value = reviewFieldValue(source, field);
      wrap.append(head, textarea); article.append(wrap);
    }
    const actions = document.createElement("div"); actions.className = "card-actions";
    const signButton = document.createElement("button"); signButton.textContent = "Regenerate Sign"; signButton.dataset.regenerateSign = sign;
    actions.append(signButton); article.append(actions); root.append(article);
  }
}

function unknownSteps(data) {
  const steps = data?.publication?.steps ?? data?.publication_steps ?? [];
  return Array.isArray(steps) ? steps.filter((step) => step?.state === "unknown_delivery") : [];
}

export async function pollExecution({
  projectId,
  executionId,
  fetcher = globalThis.fetch,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  onUpdate = () => {},
  maxPolls = 60,
  intervalMs = 2000,
}) {
  for (let attempt = 0; attempt < maxPolls; attempt += 1) {
    const response = await fetcher(`/api/projects/${encodeURIComponent(projectId)}/executions/${encodeURIComponent(executionId)}`, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Execution status HTTP ${response.status}`);
    const body = await response.json();
    const row = Array.isArray(body) ? body[0] ?? null : body;
    onUpdate(row);
    if (!row || ["finished", "failed", "cancelled"].includes(row.state)) return row;
    await sleep(intervalMs);
  }
  throw new Error("Execution status polling timed out");
}

export async function reconcileUnknownDelivery({ projectId, runId, stepKey, state, fetcher = globalThis.fetch }) {
  if (!["sent", "failed"].includes(state)) throw new Error("Reconciliation state must be sent or failed");
  const response = await fetcher(`/api/projects/${encodeURIComponent(projectId)}/publications/${encodeURIComponent(runId)}/reconcile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step_key: stepKey, state }),
  });
  if (!response.ok) throw new Error(`Reconciliation HTTP ${response.status}`);
  return response.json();
}

if (typeof document !== "undefined") {
  const state = { content: null };
  const message = (text) => { document.querySelector("#message").textContent = text; };
  const client = new CommandClient({ projectId: "caelus", onMessage: message });

  async function load() {
    const key = contentKey();
    message(`Loading ${key}…`);
    const [content, usage] = await Promise.all([
      getJson(`/api/projects/caelus/content/${encodeURIComponent(key)}`),
      getJson(`/api/projects/caelus/usage`).catch(() => []),
    ]);
    state.content = content;
    const set = content?.content_set ?? {};
    const revision = content?.revision ?? {};
    document.querySelector("#revision").textContent = `version ${set.version ?? "—"} · revision ${revision.revision_id?.slice?.(0,8) ?? "—"}`;
    document.querySelector("#approval").textContent = set.approved_revision_id ? "approved" : "review required";
    const publicationState = content?.publication?.run?.state ?? "not started";
    document.querySelector("#publication").textContent = `publication ${publicationState}`;
    document.querySelector("#usage").textContent = Array.isArray(usage) && usage.length ? `Usage samples: ${usage.length}` : "Usage metrics: no samples yet";
    document.querySelector("#health").textContent = "Cloud state: connected";
    renderCards(revision.payload ?? {}, content?.artifacts ?? []);
    const diagnostics = uniquenessDiagnostics(revision.payload ?? {});
    document.querySelector("#diagnostics").textContent = Object.keys(diagnostics).length ? `Uniqueness diagnostics: ${JSON.stringify(diagnostics)}` : "Uniqueness diagnostics: no canonical diagnostics in this revision";
    const warning = document.querySelector("#warning");
    const unknown = unknownSteps(content);
    warning.classList.toggle("hidden", unknown.length === 0);
    warning.replaceChildren();
    if (unknown.length) {
      const text = document.createElement("div");
      text.textContent = "Publication delivery is unknown. Do not resend automatically; reconcile the Telegram result first.";
      warning.append(text);
      const runId = content?.publication?.run?.publication_run_id;
      for (const step of unknown) {
        const actions = document.createElement("div"); actions.className = "warning-actions";
        const label = document.createElement("span"); label.textContent = step.step_key;
        const sent = document.createElement("button"); sent.textContent = "Confirm sent";
        const failed = document.createElement("button"); failed.textContent = "Confirm failed"; failed.className = "secondary";
        sent.addEventListener("click", () => reconcileUnknownDelivery({ projectId:"caelus", runId, stepKey:step.step_key, state:"sent" }).then(load).catch((e) => message(String(e))));
        failed.addEventListener("click", () => reconcileUnknownDelivery({ projectId:"caelus", runId, stepKey:step.step_key, state:"failed" }).then(load).catch((e) => message(String(e))));
        actions.append(label, sent, failed); warning.append(actions);
      }
    }
    message(`Loaded ${key}`);
  }

  async function submit(operation, payload = {}) {
    const set = state.content?.content_set;
    if (!set) throw new Error("Load content first");
    const body = await client.submit(operation, { contentKey: set.content_key, contentSetId: set.content_set_id, version: set.version, payload });
    message(`Execution ${body.execution_id ?? "created"}: ${operation}`);
    if (body.execution_id) {
      pollExecution({ projectId:"caelus", executionId:body.execution_id, onUpdate:(row) => message(`Execution ${body.execution_id}: ${row?.state ?? "queued"}${row?.outcome ? ` · ${row.outcome}` : ""}`) })
        .then(() => load())
        .catch((error) => message(String(error)));
    }
    return body;
  }

  document.querySelector("#load").addEventListener("click", () => load().catch((e) => message(String(e))));
  document.querySelectorAll("[data-op]").forEach((button) => button.addEventListener("click", () => {
    const operation = button.dataset.op;
    const fields = readFields();
    const payload = operation === "generate" ? { mode: "ai" } : operation === "publish" ? {} : { fields };
    submit(operation, payload).catch((e) => { if (String(e).includes("version_conflict")) return; message(String(e)); });
  }));
  document.querySelector("#cards").addEventListener("click", (event) => {
    const fieldButton = event.target.closest("[data-regenerate-field]");
    if (fieldButton) {
      submit("regenerate_field", { sign: fieldButton.dataset.sign, field: canonicalFieldName(fieldButton.dataset.regenerateField), fields: readFields() }).catch((e) => message(String(e)));
      return;
    }
    const button = event.target.closest("[data-regenerate-sign]");
    if (!button) return;
    submit("regenerate_sign", { sign: button.dataset.regenerateSign, fields: readFields() }).catch((e) => message(String(e)));
  });
  const today = aucklandDate();
  document.querySelector("#date").value = today;
  load().catch((e) => message(String(e)));
}
