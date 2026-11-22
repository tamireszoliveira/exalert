// Alerta de Cotação: PWA sem etapa de build (ES modules servidos pelo nginx).
// Rotas por hash: #/  #/novo  #/alerta/:id  #/alerta/:id/editar  #/historico  #/ajustes

import { api, ApiError, getToken, setToken } from "./api.js";
import { gauge, lineChart } from "./charts.js";
import {
  alertLabel,
  alertText,
  assetKind,
  dayLabel,
  distance,
  esc,
  fmtDateTime,
  fmtDiff,
  fmtLimit,
  fmtNumber,
  fmtPrice,
  fmtTime,
  limitDecimals,
  limitVsPrice,
  parseDecimal,
  pushPreview,
  rearmPrice,
} from "./format.js";

const VERSION = "0.1.0";
const ASSET_RE = /^([A-Z]{3,6}-[A-Z]{3,6}|\^?[A-Z][A-Z0-9.]{1,11})$/; // mesmo critério da API e do checker
const RANGES = [
  [24, "24 h"],
  [168, "7 dias"],
  [720, "30 dias"],
];

const root = document.getElementById("app");
const toastBox = document.getElementById("toast");
let flash = null; // mensagem para a próxima tela
let refreshTimer = null;

// --------------------------------------------------------------------------- ícones

const svg = (body, size = 22, extra = "") =>
  `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ${extra}>${body}</svg>`;

const ICON = {
  logo: `<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#D4AF37" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="5.25"/><path d="M1 8h14"/></svg>`,
  bell: svg('<path d="M18 9a6 6 0 0 0-12 0c0 6-2.5 7.5-2.5 7.5h17S18 15 18 9"/><path d="M10.2 20a2 2 0 0 0 3.6 0"/>'),
  clock: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
  sliders: svg('<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>'),
  back: svg('<path d="M15 5l-7 7 7 7"/>', 24, 'stroke-width="2"'),
  edit: svg('<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>', 20, 'stroke-width="2.2"'),
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>', 20),
  down: svg('<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5"/><path d="M5 20h14"/>', 20, 'stroke-width="2"'),
  up: svg('<path d="M12 20V9M7.5 13.5L12 9l4.5 4.5"/><path d="M5 4h14"/>', 20, 'stroke-width="2"'),
  rearm: svg('<path d="M19 12a7 7 0 1 1-2.05-4.95"/><path d="M19 4.5V8h-3.5"/>', 20),
  warning: svg('<path d="M12 4.5l8.5 14.5h-17L12 4.5z"/><path d="M12 10v4.5"/><path d="M12 17.3v.2"/>', 20),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 20, 'stroke-width="2"'),
};

// --------------------------------------------------------------------------- moldura

function nav(active) {
  const item = (key, href, icon, text) =>
    `<a href="${href}" class="nav-item${key === active ? " active" : ""}"${key === active ? ' aria-current="page"' : ""}>${icon}<span>${text}</span></a>`;
  return `<nav class="tabbar" aria-label="Seções">
    ${item("alertas", "#/", ICON.bell, "Alertas")}
    ${item("historico", "#/historico", ICON.clock, "Histórico")}
    ${item("ajustes", "#/ajustes", ICON.sliders, "Ajustes")}
  </nav>`;
}

function topBar(title, back, action = "") {
  return `<div class="topbar">
    <a href="${back}" class="icon-button" aria-label="Voltar">${ICON.back}</a>
    <h1 class="topbar-title">${esc(title)}</h1>
    ${action || '<span class="icon-button" aria-hidden="true"></span>'}
  </div>`;
}

function render(html, { tab } = {}) {
  const banner = flash ? `<p class="flash ${flash.kind}" role="status">${esc(flash.text)}</p>` : "";
  flash = null;
  const offline = navigator.onLine ? "" : '<p class="flash warn" role="status">Sem internet: mostrando o que deu para carregar.</p>';
  root.innerHTML = `<main class="screen${tab ? "" : " no-tabbar"}">${offline}${banner}${html}</main>${tab ? nav(tab) : ""}`;
  window.scrollTo(0, 0);
}

function toast(text) {
  toastBox.textContent = text;
  toastBox.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => toastBox.classList.remove("show"), 2600);
}

const pill = (text, kind = "") => `<span class="pill ${kind}">${esc(text)}</span>`;

function statusPill(alert) {
  if (!alert.enabled) return pill("Pausado", "muted");
  if (alert.state === "FIRED" && alert.firedAt) {
    const when = dayLabel(alert.firedAt) === "Hoje" ? `às ${fmtTime(alert.firedAt)}` : `em ${fmtDateTime(alert.firedAt)}`;
    return pill(`Disparado ${when}`, "gold");
  }
  return pill("Aguardando");
}

const sideWord = (alert) => (alert.direction === "BELOW" ? "abaixo" : "acima");

// --------------------------------------------------------------------------- Alertas

function alertCard(alert) {
  const quote = alert.lastQuote;
  const price = quote ? Number(quote.price) : null;
  const threshold = Number(alert.threshold);
  const fired = alert.enabled && alert.state === "FIRED";
  let right = "sem cotação ainda";
  if (fired) {
    right = `rearma em ${esc(fmtPrice(alert.asset, rearmPrice(alert)))}`;
  } else if (price !== null) {
    const { gap } = distance(alert, price);
    right = gap > 0 ? `faltam ${esc(fmtDiff(alert.asset, gap))}` : "no limite";
  }
  const verb = fired ? "Avisou" : "Avisar";
  return `<a class="card alert-card${fired ? " fired" : ""}${alert.enabled ? "" : " paused"}" href="#/alerta/${esc(alert.id)}">
    <div class="row-between">
      <div class="stack-2">
        <span class="card-title">${esc(alertLabel(alert))}</span>
        <span class="subtle">${esc(alert.asset)} · ${assetKind(alert.asset)}</span>
      </div>
      ${statusPill(alert)}
    </div>
    <div class="row-between baseline">
      <span class="price">${price === null ? "—" : esc(fmtPrice(alert.asset, price))}</span>
      <span class="subtle">${quote ? `cotação das ${fmtTime(quote.quotedAt)}` : ""}</span>
    </div>
    ${price === null ? "" : gauge({ asset: alert.asset, price, threshold, direction: alert.direction })}
    <div class="row-between small">
      <span class="muted">${verb} ${sideWord(alert)} de <strong class="gold">${esc(fmtLimit(alert.asset, threshold))}</strong></span>
      <span class="${fired ? "muted" : ""}">${right}</span>
    </div>
  </a>`;
}

async function viewAlerts() {
  const alerts = await api.alerts();
  const assets = new Set(alerts.map((a) => a.asset));
  const latest = alerts
    .map((a) => a.lastQuote?.quotedAt)
    .filter(Boolean)
    .sort()
    .pop();
  const subtitle = alerts.length
    ? `${assets.size} ${assets.size === 1 ? "ativo" : "ativos"}${latest ? ` · última cotação às ${fmtTime(latest)}` : ""}`
    : "Nenhum alerta ainda";
  const list = alerts.length
    ? `<div class="stack-12">${alerts.map(alertCard).join("")}</div>`
    : `<div class="card empty"><p>Crie um alerta para o dólar ou para uma ação da B3 e receba um push quando o preço cruzar o valor.</p></div>`;
  render(
    `<header class="stack-10">
      <div class="brand">${ICON.logo}<span>ALERTA DE COTAÇÃO</span></div>
      <div class="stack-4"><h1 class="title">Alertas</h1><p class="muted">${esc(subtitle)}</p></div>
    </header>
    ${list}
    <div class="grow"></div>
    <a class="button primary" href="#/novo">${ICON.plus}<span>Novo alerta</span></a>`,
    { tab: "alertas" },
  );
  scheduleRefresh(viewAlerts);
}

// --------------------------------------------------------------------------- Detalhe

async function viewDetail(id, hours = 24) {
  const alert = await api.alert(id);
  const [series, events] = await Promise.all([api.quotes(alert.asset, hours), api.events(200)]);
  const quote = alert.lastQuote;
  const price = quote ? Number(quote.price) : null;
  const threshold = Number(alert.threshold);
  const mine = events.filter((e) => e.alertId === alert.id).slice(0, 5);
  const rows = [["Condição", `${sideWord(alert)} de ${fmtLimit(alert.asset, threshold)}`]];
  if (price !== null) {
    const { gap, pct } = distance(alert, price);
    rows.push(["Distância agora", gap > 0 ? `${fmtDiff(alert.asset, gap)} · ${fmtNumber(Math.abs(pct), 1)}%` : "passou do limite"]);
  }
  rows.push(["Rearme", `volta a valer em ${fmtPrice(alert.asset, rearmPrice(alert))} (${fmtNumber(Number(alert.rearmPct), 1)}%)`]);
  rows.push(["Último disparo", alert.firedAt ? fmtDateTime(alert.firedAt) : "nunca"]);
  const ranges = RANGES.map(
    ([h, text]) => `<button type="button" class="segment" data-hours="${h}" aria-pressed="${h === hours}">${text}</button>`,
  ).join("");
  render(
    `${topBar(alertLabel(alert), "#/", `<a href="#/alerta/${esc(alert.id)}/editar" class="icon-button" aria-label="Editar alerta">${ICON.edit}</a>`)}
    <section class="stack-6">
      <span class="subtle">${esc(alert.asset)} · ${assetKind(alert.asset)}</span>
      <div class="row-between"><span class="price big">${price === null ? "—" : esc(fmtPrice(alert.asset, price))}</span>${statusPill(alert)}</div>
      <span class="subtle">${quote ? `Cotação das ${fmtTime(quote.quotedAt)} · ${esc(quote.source)}` : "Ainda sem cotação: o checker busca no próximo ciclo."}</span>
    </section>
    <section class="card stack-12">
      <div class="segments three" role="group" aria-label="Período">${ranges}</div>
      ${lineChart({ asset: alert.asset, points: series.points, threshold, direction: alert.direction, hours })}
    </section>
    <section class="card list">${rows.map(([k, v]) => `<div class="list-row"><span class="muted">${k}</span><strong>${esc(v)}</strong></div>`).join("")}</section>
    ${mine.length ? `<section class="stack-8"><h2 class="section-title">Últimos eventos</h2><div class="card list">${mine.map((e) => eventRow(e, { withDate: true })).join("")}</div></section>` : ""}`,
  );
  root.querySelectorAll("[data-hours]").forEach((button) =>
    button.addEventListener("click", () => run(() => viewDetail(id, Number(button.dataset.hours)))),
  );
  scheduleRefresh(() => viewDetail(id, hours));
}

// --------------------------------------------------------------------------- Formulário

async function viewForm(id) {
  const editing = id ? await api.alert(id) : null;
  const value = {
    asset: editing?.asset ?? "",
    name: editing?.name ?? "",
    direction: editing?.direction ?? "BELOW",
    threshold: editing ? fmtNumber(Number(editing.threshold), limitDecimals(Number(editing.threshold))) : "",
    rearmPct: String(Number(editing?.rearmPct ?? 0.5)).replace(".", ","),
    enabled: editing?.enabled ?? true,
  };
  let quote = editing?.lastQuote ? { ...editing.lastQuote, asset: editing.asset } : null;
  let checked = editing?.asset ?? null; // ativo cuja última cotação já foi consultada
  const back = editing ? `#/alerta/${esc(editing.id)}` : "#/";
  render(
    `${topBar(editing ? "Editar alerta" : "Novo alerta", back)}
    <form class="stack-18" novalidate>
      <div class="field">
        <label for="asset">Ativo</label>
        <div class="input">${ICON.search}<input id="asset" name="asset" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="USD-BRL ou VALE3" value="${esc(value.asset)}" required></div>
        <span class="hint" id="asset-hint">Par de moedas (USD-BRL) ou ticker da B3 (VALE3)</span>
      </div>
      <div class="field">
        <span class="label">Avisar quando o preço ficar</span>
        <div class="segments" role="group" aria-label="Direção">
          <button type="button" class="segment" data-direction="BELOW" aria-pressed="${value.direction === "BELOW"}">Abaixo de</button>
          <button type="button" class="segment" data-direction="ABOVE" aria-pressed="${value.direction === "ABOVE"}">Acima de</button>
        </div>
      </div>
      <div class="field">
        <label for="threshold">Valor do alerta</label>
        <div class="input money"><span class="currency">R$</span><input id="threshold" name="threshold" inputmode="decimal" autocomplete="off" placeholder="0,00" value="${esc(value.threshold)}" required></div>
        <span class="hint" id="threshold-hint"></span>
      </div>
      <div class="field">
        <label for="name">Nome (opcional)</label>
        <div class="input"><input id="name" name="name" maxlength="60" autocomplete="off" placeholder="ex.: Dólar" value="${esc(value.name)}"></div>
        <span class="hint" id="name-hint"></span>
      </div>
      <div class="field">
        <label for="rearmPct">Margem de rearme (%)</label>
        <div class="input"><input id="rearmPct" name="rearmPct" inputmode="decimal" autocomplete="off" value="${esc(value.rearmPct)}"></div>
        <span class="hint" id="rearmPct-hint"></span>
      </div>
      ${editing ? `<label class="switch-row"><span class="stack-2"><span>Alerta ativo</span><span class="hint">Pausado, o checker ignora este alerta.</span></span><input type="checkbox" id="enabled" class="switch" ${value.enabled ? "checked" : ""}></label>` : ""}
      <div class="field">
        <span class="label">Como a notificação chega</span>
        <div class="preview">${ICON.bell}<div class="stack-2" id="preview"></div></div>
      </div>
      <p class="flash error" id="form-error" hidden></p>
      <button type="submit" class="button primary">${editing ? "Salvar" : "Criar alerta"}</button>
      ${editing ? '<button type="button" class="button danger" id="delete">Excluir alerta</button>' : ""}
    </form>`,
  );

  const form = root.querySelector("form");
  const $ = (selector) => form.querySelector(selector);
  let direction = value.direction;

  const read = () => ({
    asset: $("#asset").value.trim().toUpperCase(),
    name: $("#name").value.trim() || null,
    direction,
    threshold: parseDecimal($("#threshold").value),
    rearmPct: parseDecimal($("#rearmPct").value),
    enabled: editing ? $("#enabled").checked : true,
  });

  const update = () => {
    const draft = read();
    const valid = ASSET_RE.test(draft.asset);
    const known = quote && quote.asset === draft.asset ? quote : null;
    let assetHint = "Par de moedas (USD-BRL) ou ticker da B3 (VALE3)";
    if (valid && known) assetHint = `${assetKind(draft.asset)} · ${fmtPrice(draft.asset, Number(known.price))} agora`;
    else if (valid) assetHint = `${assetKind(draft.asset)}${checked === draft.asset ? " · ainda sem cotação registrada" : ""}`;
    $("#asset-hint").textContent = assetHint;
    const hasLimit = Number.isFinite(draft.threshold) && draft.threshold > 0;
    $("#threshold-hint").textContent = hasLimit && known ? limitVsPrice(draft.threshold, Number(known.price)) : "";
    const pct = Number.isFinite(draft.rearmPct) ? draft.rearmPct : 0.5;
    $("#rearmPct-hint").textContent = hasLimit && valid
      ? `Depois de avisar, volta a valer quando o preço passar de ${fmtPrice(draft.asset, rearmPrice({ ...draft, rearmPct: pct }))}`
      : "Quanto o preço precisa voltar além do limite para o alerta valer de novo";
    const preview = $("#preview");
    if (hasLimit && valid) {
      const sample = known ?? { price: draft.threshold, quotedAt: null, source: draft.asset.includes("-") ? "AwesomeAPI" : "brapi" };
      const { title, lines } = pushPreview({ ...draft, threshold: draft.threshold }, sample);
      preview.innerHTML = `<strong>${esc(title)}</strong>${lines.map((line) => `<span class="muted">${esc(line)}</span>`).join("")}`;
    } else {
      preview.innerHTML = '<span class="muted">Preencha ativo e valor para ver o exemplo.</span>';
    }
  };

  const loadQuote = async () => {
    const asset = $("#asset").value.trim().toUpperCase();
    if (!ASSET_RE.test(asset) || checked === asset) return update();
    try {
      const series = await api.quotes(asset, 720);
      const last = series.points[series.points.length - 1];
      quote = last ? { asset, price: last.price, quotedAt: last.t, source: asset.includes("-") ? "AwesomeAPI" : "brapi" } : null;
      checked = asset;
    } catch {
      quote = null;
    }
    update();
  };

  form.querySelectorAll("[data-direction]").forEach((button) =>
    button.addEventListener("click", () => {
      direction = button.dataset.direction;
      form.querySelectorAll("[data-direction]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      update();
    }),
  );
  form.addEventListener("input", (event) => {
    event.target.closest(".field")?.classList.remove("invalid");
    update();
  });
  $("#asset").addEventListener("change", loadQuote);
  update();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    form.querySelectorAll(".field.invalid").forEach((field) => field.classList.remove("invalid"));
    const draft = read();
    const problems = {};
    if (!ASSET_RE.test(draft.asset)) problems.asset = "Use um par de moedas (USD-BRL) ou um ticker da B3 (VALE3).";
    if (!(draft.threshold > 0)) problems.threshold = "Informe um valor maior que zero, ex.: 5,00.";
    if (!(draft.rearmPct >= 0 && draft.rearmPct <= 50)) problems.rearmPct = "Use um número entre 0 e 50.";
    if (Object.keys(problems).length) return showErrors(form, problems);
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const saved = editing ? await api.updateAlert(editing.id, draft) : await api.createAlert(draft);
      flash = { kind: "ok", text: editing ? "Alerta salvo." : "Alerta criado. O checker avalia no próximo ciclo." };
      location.hash = `#/alerta/${saved.id}`;
    } catch (error) {
      button.disabled = false;
      if (error instanceof ApiError && error.status === 400 && Object.keys(error.fieldErrors).length) {
        showErrors(form, error.fieldErrors);
      } else {
        handleError(error, form.querySelector("#form-error"));
      }
    }
  });

  form.querySelector("#delete")?.addEventListener("click", async () => {
    if (!window.confirm(`Excluir "${alertText(editing)}"? O histórico continua guardado.`)) return;
    try {
      await api.deleteAlert(editing.id);
      flash = { kind: "ok", text: "Alerta excluído." };
      location.hash = "#/";
    } catch (error) {
      handleError(error, form.querySelector("#form-error"));
    }
  });
}

function showErrors(form, errors) {
  let first = null;
  for (const [field, message] of Object.entries(errors)) {
    const hint = form.querySelector(`#${field}-hint`);
    if (!hint) continue;
    hint.textContent = message;
    hint.closest(".field")?.classList.add("invalid");
    first ??= form.querySelector(`#${field}`);
  }
  first?.focus();
}

// --------------------------------------------------------------------------- Histórico

const EVENT_ICON = {
  FIRED: (e) => `<span class="badge gold">${e.direction === "ABOVE" ? ICON.up : ICON.down}</span>`,
  REARMED: () => `<span class="badge">${ICON.rearm}</span>`,
  SOURCE_DOWN: () => `<span class="badge">${ICON.warning}</span>`,
  SOURCE_UP: () => `<span class="badge">${ICON.check}</span>`,
};

function eventRow(event, { withDate = false } = {}) {
  const icon = (EVENT_ICON[event.type] ?? EVENT_ICON.REARMED)(event);
  let detail = String(event.message || "").split("\n")[0];
  if (event.type === "FIRED" && event.price != null) detail = `Disparou em ${fmtPrice(event.asset, Number(event.price))}`;
  if (event.push && !event.deliveredAt) detail += " · push pendente";
  return `<div class="list-row event">
    ${icon}
    <div class="stack-2 grow"><strong>${esc(event.title)}</strong><span class="muted small">${esc(detail)}</span></div>
    <span class="muted small tabular">${withDate && dayLabel(event.occurredAt) !== "Hoje" ? fmtDateTime(event.occurredAt) : fmtTime(event.occurredAt)}</span>
  </div>`;
}

async function viewHistory() {
  const events = await api.events(100);
  const groups = new Map();
  for (const event of events) {
    const label = dayLabel(event.occurredAt);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(event);
  }
  const sections = [...groups.entries()]
    .map(
      ([label, items]) => `<section class="stack-8">
      <h2 class="section-title">${esc(label)}</h2>
      <div class="card list${items.some((e) => e.type === "FIRED") ? " fired" : ""}">${items.map((e) => eventRow(e)).join("")}</div>
    </section>`,
    )
    .join("");
  render(
    `<header class="stack-4"><h1 class="title">Histórico</h1><p class="muted">Disparos, rearmes e falhas de fonte</p></header>
    ${sections || '<div class="card empty"><p>Nada por aqui ainda. Os disparos aparecem assim que o checker avisar.</p></div>'}`,
    { tab: "historico" },
  );
  scheduleRefresh(viewHistory);
}

// --------------------------------------------------------------------------- Ajustes

function viewSettings() {
  const connected = Boolean(getToken());
  render(
    `<header class="stack-4"><h1 class="title">Ajustes</h1><p class="muted">Conexão com a API e notificações</p></header>
    <form class="card stack-12" id="token-form" novalidate>
      <div class="field">
        <label for="token">Token de acesso (APP_TOKEN)</label>
        <div class="input"><input id="token" type="password" autocomplete="off" spellcheck="false" placeholder="${connected ? "•••••••• (salvo neste aparelho)" : "cole o APP_TOKEN"}"></div>
        <span class="hint" id="token-hint">Fica só neste aparelho. É o mesmo valor configurado na API.</span>
      </div>
      <button type="submit" class="button primary">Salvar e testar</button>
      ${connected ? '<button type="button" class="button ghost" id="logout">Desconectar este aparelho</button>' : ""}
    </form>
    <section class="card stack-8">
      <h2 class="card-title">Notificações</h2>
      <p class="muted">Os avisos saem do checker pelo app <strong>ntfy</strong>, no tópico configurado em NTFY_TOPIC. Instale o ntfy no iPhone e assine o mesmo tópico.</p>
      <p class="subtle">Push direto por este app (Web Push) entra quando ele estiver publicado com HTTPS.</p>
    </section>
    <section class="card stack-8">
      <h2 class="card-title">Instalar no iPhone</h2>
      <p class="muted">No Safari, toque em Compartilhar e depois em <strong>Adicionar à Tela de Início</strong>. O app abre em tela cheia, como um app nativo.</p>
    </section>
    <p class="subtle center">Alerta de Cotação ${VERSION}</p>`,
    { tab: "ajustes" },
  );
  const form = root.querySelector("#token-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = form.querySelector("#token").value.trim();
    const hint = form.querySelector("#token-hint");
    if (!token) {
      hint.textContent = "Cole o token antes de salvar.";
      form.querySelector(".field").classList.add("invalid");
      return;
    }
    try {
      await api.check(token);
      setToken(token);
      flash = { kind: "ok", text: "Conectado." };
      location.hash = "#/";
    } catch (error) {
      form.querySelector(".field").classList.add("invalid");
      hint.textContent = error instanceof ApiError && error.status === 401 ? "Token recusado pela API." : error.message;
    }
  });
  form.querySelector("#logout")?.addEventListener("click", () => {
    setToken("");
    toast("Token removido deste aparelho.");
    viewSettings();
  });
}

// --------------------------------------------------------------------------- roteamento

function scheduleRefresh(view) {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    if (document.visibilityState === "visible") view().catch(() => {});
  }, 60_000);
}

function handleError(error, target) {
  if (error instanceof ApiError && error.status === 401) {
    flash = { kind: "warn", text: "A API recusou o token salvo. Cole o APP_TOKEN de novo." };
    location.hash = "#/ajustes";
    return;
  }
  const text = error instanceof ApiError ? error.message : "Algo deu errado. Tente de novo.";
  if (target) {
    target.textContent = text;
    target.hidden = false;
    return;
  }
  render(
    `<header class="stack-4"><h1 class="title">Ops</h1></header>
    <div class="card stack-12"><p>${esc(text)}</p><button type="button" class="button ghost" id="retry">Tentar de novo</button></div>`,
    { tab: "alertas" },
  );
  root.querySelector("#retry").addEventListener("click", route);
}

async function run(view) {
  clearInterval(refreshTimer);
  try {
    await view();
  } catch (error) {
    if (!(error instanceof ApiError)) console.error(error);
    handleError(error);
  }
}

const ROUTES = [
  [/^#\/?$/, () => viewAlerts()],
  [/^#\/novo$/, () => viewForm(null)],
  [/^#\/alerta\/([\w-]+)$/, (id) => viewDetail(id)],
  [/^#\/alerta\/([\w-]+)\/editar$/, (id) => viewForm(id)],
  [/^#\/historico$/, () => viewHistory()],
  [/^#\/ajustes$/, () => viewSettings()],
];

function route() {
  const hash = location.hash || "#/";
  if (!getToken() && hash !== "#/ajustes") {
    flash ??= { kind: "info", text: "Para começar, cole o APP_TOKEN da sua API." };
    location.replace("#/ajustes");
    return;
  }
  for (const [pattern, view] of ROUTES) {
    const match = hash.match(pattern);
    if (match) return run(() => view(...match.slice(1)));
  }
  location.replace("#/");
}

window.addEventListener("hashchange", route);
window.addEventListener("online", route);
window.addEventListener("offline", () => toast("Sem internet."));
document.addEventListener("visibilitychange", () => {
  // voltar ao app atualiza as telas de leitura (nunca um formulário pela metade)
  const reading = /^#\/?$|^#\/alerta\/[\w-]+$|^#\/historico$/.test(location.hash || "#/");
  if (document.visibilityState === "visible" && getToken() && reading) route();
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

route();
