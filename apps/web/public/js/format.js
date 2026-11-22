// Formatação no padrão brasileiro, a mesma do checker (services/checker/alerta_checker/core.py):
// o app mostra exatamente os textos que chegam no push.

export const TZ = "America/Sao_Paulo";

export const isFx = (asset) => asset.includes("-");

const numberFormats = new Map();

export function fmtNumber(value, decimals) {
  let format = numberFormats.get(decimals);
  if (!format) {
    format = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    numberFormats.set(decimals, format);
  }
  return format.format(value);
}

function currency(asset, text) {
  const quote = isFx(asset) ? asset.split("-")[1] : "BRL";
  return quote.startsWith("BRL") ? `R$ ${text}` : `${text} ${quote}`; // BRLT = real turismo
}

export const priceDecimals = (asset) => (isFx(asset) ? 4 : 2);

export const fmtPrice = (asset, price) => currency(asset, fmtNumber(price, priceDecimals(asset)));

/** Casas decimais suficientes para o limite: 5 -> 2, 4.955 -> 3 (como fmt_limit no checker). */
export function limitDecimals(limit) {
  for (let decimals = 2; decimals < 6; decimals += 1) {
    if (Number(limit.toFixed(decimals)) === limit) return decimals;
  }
  return 6;
}

export const fmtLimit = (asset, limit) => currency(asset, fmtNumber(limit, limitDecimals(limit)));

/** Valor em reais com as casas que fazem sentido para a diferença (0,18 ou 0,0050). */
export function fmtDiff(asset, value) {
  const decimals = Math.abs(value) >= 0.1 ? 2 : priceDecimals(asset);
  return currency(asset, fmtNumber(Math.abs(value), decimals));
}

export const alertLabel = (alert) => alert.name || alert.asset;

/** "Dólar abaixo de R$ 5,00": o mesmo título do push. */
export function alertText(alert) {
  const side = alert.direction === "BELOW" ? "abaixo" : "acima";
  return `${alertLabel(alert)} ${side} de ${fmtLimit(alert.asset, Number(alert.threshold))}`;
}

export const assetKind = (asset) => (isFx(asset) ? "câmbio comercial" : "B3");

const timeFormat = new Intl.DateTimeFormat("pt-BR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const dateFormat = new Intl.DateTimeFormat("pt-BR", { timeZone: TZ, day: "2-digit", month: "2-digit" });
const dayKeyFormat = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });

export const fmtTime = (iso) => timeFormat.format(new Date(iso));
export const fmtDate = (iso) => dateFormat.format(new Date(iso));
/** "10/11 14:32", como fmt_dt no checker. */
export const fmtDateTime = (iso) => `${fmtDate(iso)} ${fmtTime(iso)}`;

/** "Hoje", "Ontem" ou "08/11", no fuso de Brasília. */
export function dayLabel(iso, now = new Date()) {
  const key = dayKeyFormat.format(new Date(iso));
  if (key === dayKeyFormat.format(now)) return "Hoje";
  if (key === dayKeyFormat.format(new Date(now.getTime() - 86_400_000))) return "Ontem";
  return fmtDate(iso);
}

/** "65,00", "1.234,5", "65.5" ou "R$ 65" -> número; texto inválido -> NaN. */
export function parseDecimal(text) {
  const raw = String(text ?? "").replace(/R\$|\s/g, "");
  if (!raw) return Number.NaN;
  const normalized = raw.includes(",") ? raw.replace(/\./g, "").replace(",", ".") : raw;
  return /^\d+(\.\d+)?$/.test(normalized) ? Number(normalized) : Number.NaN;
}

/** Preço em que o alerta disparado volta a valer. */
export function rearmPrice(alert) {
  const limit = Number(alert.threshold);
  const pct = Number(alert.rearmPct ?? 0.5);
  return alert.direction === "BELOW" ? limit * (1 + pct / 100) : limit * (1 - pct / 100);
}

/** Quanto falta para disparar (gap > 0) ou quanto já passou do limite (gap <= 0). */
export function distance(alert, price) {
  const limit = Number(alert.threshold);
  const gap = alert.direction === "BELOW" ? price - limit : limit - price;
  return { gap, pct: (gap / price) * 100 };
}

/** "2,1% abaixo do preço atual" para o formulário. */
export function limitVsPrice(limit, price) {
  const pct = ((limit - price) / price) * 100;
  if (Math.abs(pct) < 0.005) return "igual ao preço atual";
  return `${fmtNumber(Math.abs(pct), 1)}% ${pct < 0 ? "abaixo" : "acima"} do preço atual`;
}

/** Push como o checker vai mandar (título e linhas). */
export function pushPreview(alert, quote) {
  const lines = [`Agora: ${fmtPrice(alert.asset, Number(quote.price))}`];
  lines.push(quote.quotedAt ? `Cotação de ${fmtDateTime(quote.quotedAt)} (${quote.source})` : `Fonte: ${quote.source}`);
  return { title: alertText(alert), lines };
}

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Escapa texto vindo da API antes de ir para o HTML. */
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);
