// Gráficos em SVG montados como texto: sem biblioteca e fáceis de testar no Node.
import { esc, fmtNumber, fmtTime, fmtDate, priceDecimals } from "./format.js";

const C = { track: "#20345C", gold: "#D4AF37", text: "#F4EFE3", muted: "#AAB5C9", subtle: "#8593AD", card: "#0D1930" };
const r1 = (value) => Math.round(value * 10) / 10;

/**
 * Barra do card: o limite fica a 1/3 (alerta "abaixo") ou 2/3 ("acima") e o preço atual é o ponto.
 * A faixa dourada é a zona que dispara.
 */
export function gauge({ asset, price, threshold, direction, width = 316 }) {
  const below = direction === "BELOW";
  const marker = below ? width / 3 : (2 * width) / 3;
  const span = Math.max(Math.abs(price - threshold) * 1.6, threshold * 0.02);
  const x = Math.min(width - 6, Math.max(6, marker + ((price - threshold) / span) * (width / 3)));
  const zone = below ? [0, marker] : [marker, width - marker];
  const decimals = Math.min(priceDecimals(asset), 2);
  const limitText = fmtNumber(threshold, decimals);
  const priceText = fmtNumber(price, decimals);
  // rótulos não se sobrepõem: se o ponto estiver colado no limite, afasta os textos
  let limitX = marker;
  let priceX = x;
  if (Math.abs(priceX - limitX) < 40) {
    const shift = (40 - Math.abs(priceX - limitX)) / 2;
    const dir = priceX >= limitX ? 1 : -1;
    priceX = Math.min(width - 16, Math.max(16, priceX + shift * dir));
    limitX = Math.min(width - 16, Math.max(16, limitX - shift * dir));
  }
  const label = `Preço atual em ${priceText}, ${price < threshold ? "abaixo" : "acima"} do alerta em ${limitText}`;
  return `<svg class="gauge" role="img" aria-label="${esc(label)}" viewBox="0 0 ${width} 30" preserveAspectRatio="none">
<rect x="0" y="7" width="${width}" height="4" rx="2" fill="${C.track}"/>
<rect x="${r1(zone[0])}" y="7" width="${r1(zone[1])}" height="4" rx="2" fill="${C.gold}" fill-opacity="0.5"/>
<line x1="${r1(marker)}" y1="1" x2="${r1(marker)}" y2="17" stroke="${C.gold}" stroke-width="2" stroke-linecap="round"/>
<circle cx="${r1(x)}" cy="9" r="5" fill="${C.text}" stroke="${C.card}" stroke-width="2"/>
<text x="${r1(limitX)}" y="29" text-anchor="middle" font-size="11" fill="${C.gold}">${limitText}</text>
<text x="${r1(priceX)}" y="29" text-anchor="middle" font-size="11" fill="${C.muted}">${priceText}</text>
</svg>`;
}

/** Passo "redondo" (1, 2 ou 5 vezes potência de 10) para as linhas de grade. */
export function niceStep(raw) {
  const exponent = Math.floor(Math.log10(raw));
  const base = 10 ** exponent;
  const fraction = raw / base;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * base;
}

/**
 * Linha de preço com o limite tracejado e a zona de disparo sombreada.
 * points: [{t: ISO, price}] em ordem; o eixo x cobre as últimas `hours` horas até `now`.
 */
export function lineChart({ asset, points, threshold, direction, hours, now = Date.now(), width = 316, height = 176 }) {
  const left = 40;
  const right = width;
  const top = 8;
  const bottom = height - 36;
  const t1 = now;
  const t0 = now - hours * 3_600_000;
  const prices = points.map((p) => Number(p.price));
  let min = Math.min(threshold, ...prices);
  let max = Math.max(threshold, ...prices);
  const pad = Math.max((max - min) * 0.15, threshold * 0.004);
  min -= pad;
  max += pad;
  const step = niceStep((max - min) / 4);
  const decimals = Math.max(0, Math.min(4, -Math.floor(Math.log10(step))));
  const y = (value) => bottom - ((value - min) / (max - min)) * (bottom - top);
  const x = (iso) => left + ((new Date(iso).getTime() - t0) / (t1 - t0)) * (right - left);

  const parts = [];
  const zoneTop = direction === "BELOW" ? y(threshold) : top;
  const zoneBottom = direction === "BELOW" ? bottom : y(threshold);
  parts.push(`<rect x="${left}" y="${r1(zoneTop)}" width="${right - left}" height="${r1(zoneBottom - zoneTop)}" fill="${C.gold}" fill-opacity="0.1"/>`);
  for (let tick = Math.ceil(min / step) * step; tick <= max; tick += step) {
    const ty = r1(y(tick));
    parts.push(`<line x1="${left}" y1="${ty}" x2="${right}" y2="${ty}" stroke="${C.track}" stroke-width="1"/>`);
    parts.push(`<text x="0" y="${r1(ty + 4)}" font-size="11" fill="${C.subtle}">${fmtNumber(tick, decimals)}</text>`);
  }
  const ty = r1(y(threshold));
  parts.push(`<line x1="${left}" y1="${ty}" x2="${right}" y2="${ty}" stroke="${C.gold}" stroke-width="1.5" stroke-dasharray="5 4"/>`);
  const zoneText = direction === "BELOW" ? "avisa daqui para baixo" : "avisa daqui para cima";
  const zoneTextY = direction === "BELOW" ? Math.min(bottom - 4, ty + 15) : Math.max(top + 11, ty - 6);
  parts.push(`<text x="${right}" y="${r1(zoneTextY)}" text-anchor="end" font-size="11" fill="${C.gold}">${zoneText}</text>`);

  const visible = points.filter((p) => new Date(p.t).getTime() >= t0);
  if (visible.length) {
    const d = visible.map((p, i) => `${i ? "L" : "M"}${r1(x(p.t))} ${r1(y(Number(p.price)))}`).join("");
    parts.push(`<path d="${d}" fill="none" stroke="${C.text}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`);
    const last = visible[visible.length - 1];
    parts.push(`<circle cx="${r1(x(last.t))}" cy="${r1(y(Number(last.price)))}" r="4.5" fill="${C.text}" stroke="${C.card}" stroke-width="2"/>`);
  } else {
    parts.push(`<text x="${(left + right) / 2}" y="${(top + bottom) / 2}" text-anchor="middle" font-size="12" fill="${C.muted}">Sem cotações neste período</text>`);
  }

  for (let i = 0; i <= 3; i += 1) {
    const t = t0 + ((t1 - t0) * i) / 3;
    const iso = new Date(t).toISOString();
    const text = hours <= 24 ? fmtTime(iso) : fmtDate(iso);
    const anchor = i === 0 ? "start" : i === 3 ? "end" : "middle";
    parts.push(`<text x="${r1(left + ((right - left) * i) / 3)}" y="${height - 8}" text-anchor="${anchor}" font-size="11" fill="${C.subtle}">${text}</text>`);
  }

  const last = visible.length ? Number(visible[visible.length - 1].price) : null;
  const summary = last === null ? "sem cotações no período" : `último preço ${fmtNumber(last, priceDecimals(asset))}`;
  const label = `${asset}: ${summary}; linha do alerta em ${fmtNumber(threshold, priceDecimals(asset))}`;
  return `<svg class="chart" role="img" aria-label="${esc(label)}" viewBox="0 0 ${width} ${height}">${parts.join("")}</svg>`;
}
