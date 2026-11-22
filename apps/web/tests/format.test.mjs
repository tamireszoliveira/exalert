// Testes do PWA que rodam sem navegador:  node --test apps/web/tests/
// Os casos de formatação são os mesmos do checker (services/checker/tests/test_cron.py),
// para o app e o push mostrarem exatamente o mesmo texto.
import assert from "node:assert/strict";
import { test } from "node:test";

import { gauge, lineChart, niceStep } from "../public/js/charts.js";
import {
  alertText,
  dayLabel,
  distance,
  esc,
  fmtDateTime,
  fmtDiff,
  fmtLimit,
  fmtNumber,
  fmtPrice,
  limitVsPrice,
  parseDecimal,
  pushPreview,
  rearmPrice,
} from "../public/js/format.js";

const dolar = { asset: "USD-BRL", name: "Dólar", direction: "BELOW", threshold: 5, rearmPct: 0.5 };
const valeAlto = { asset: "VALE3", name: null, direction: "ABOVE", threshold: 80, rearmPct: 0.5 };

test("números e preços no padrão brasileiro, iguais ao checker", () => {
  assert.equal(fmtNumber(1234.5, 2), "1.234,50");
  assert.equal(fmtPrice("USD-BRL", 4.987), "R$ 4,9870");
  assert.equal(fmtPrice("VALE3", 61.2), "R$ 61,20");
  assert.equal(fmtPrice("EUR-USD", 1.0853), "1,0853 USD");
  assert.equal(fmtLimit("USD-BRL", 5.0), "R$ 5,00");
  assert.equal(fmtLimit("USD-BRL", 4.955), "R$ 4,955");
  assert.equal(fmtLimit("VALE3", 65.0), "R$ 65,00");
});

test("título do alerta é o título do push", () => {
  assert.equal(alertText(dolar), "Dólar abaixo de R$ 5,00");
  assert.equal(alertText(valeAlto), "VALE3 acima de R$ 80,00");
  const preview = pushPreview(dolar, { price: 4.987, quotedAt: "2026-11-10T17:32:00Z", source: "AwesomeAPI" });
  assert.deepEqual(preview, {
    title: "Dólar abaixo de R$ 5,00",
    lines: ["Agora: R$ 4,9870", "Cotação de 10/11 14:32 (AwesomeAPI)"],
  });
});

test("datas no fuso de Brasília", () => {
  assert.equal(fmtDateTime("2026-11-10T17:32:00Z"), "10/11 14:32");
  const now = new Date("2026-11-10T15:00:00Z"); // 12:00 em Brasília
  assert.equal(dayLabel("2026-11-10T03:30:00Z", now), "Hoje"); // 00:30 BRT
  assert.equal(dayLabel("2026-11-10T02:30:00Z", now), "Ontem"); // 23:30 BRT do dia 9
  assert.equal(dayLabel("2026-11-08T12:00:00Z", now), "08/11");
});

test("valor digitado com vírgula, ponto ou R$", () => {
  assert.equal(parseDecimal("65,00"), 65);
  assert.equal(parseDecimal("1.234,5"), 1234.5);
  assert.equal(parseDecimal("R$ 4,955"), 4.955);
  assert.equal(parseDecimal("65.5"), 65.5);
  for (const junk of ["", "abc", "1,2,3", "-5", null]) assert.ok(Number.isNaN(parseDecimal(junk)), String(junk));
});

test("rearme, distância e comparação com o preço", () => {
  assert.ok(Math.abs(rearmPrice(dolar) - 5.025) < 1e-9);
  assert.ok(Math.abs(rearmPrice(valeAlto) - 79.6) < 1e-9);
  assert.ok(Math.abs(distance(dolar, 5.18).gap - 0.18) < 1e-9);
  assert.ok(distance(dolar, 4.99).gap < 0);
  assert.equal(fmtDiff("USD-BRL", 0.18), "R$ 0,18");
  assert.equal(fmtDiff("USD-BRL", 0.005), "R$ 0,0050");
  assert.equal(limitVsPrice(65, 66.4), "2,1% abaixo do preço atual");
  assert.equal(limitVsPrice(80, 70), "14,3% acima do preço atual");
});

test("texto da API é escapado", () => {
  assert.equal(esc('<img src=x onerror="a()">'), "&lt;img src=x onerror=&quot;a()&quot;&gt;");
  assert.equal(esc(null), "");
});

test("barra do card: limite a 1/3 (abaixo) e 2/3 (acima)", () => {
  const below = gauge({ asset: "USD-BRL", price: 5.18, threshold: 5, direction: "BELOW" });
  assert.match(below, /<line x1="105.3"/);
  assert.match(below, />5,00</);
  assert.match(below, />5,18</);
  const above = gauge({ asset: "VALE3", price: 70, threshold: 80, direction: "ABOVE" });
  assert.match(above, /<line x1="210.7"/);
  const touching = gauge({ asset: "USD-BRL", price: 5.0001, threshold: 5, direction: "BELOW" });
  const labels = [...touching.matchAll(/<text x="([\d.]+)"/g)].map((m) => Number(m[1]));
  assert.ok(Math.abs(labels[0] - labels[1]) >= 39, `rótulos colados: ${labels}`);
});

test("gráfico de linha: limite, zona de disparo e período vazio", () => {
  assert.equal(niceStep(0.07), 0.1);
  assert.equal(niceStep(0.3), 0.5);
  assert.equal(niceStep(12), 20);
  const now = Date.parse("2026-11-10T21:00:00Z");
  const points = [
    { t: "2026-11-10T12:00:00Z", price: 5.22 },
    { t: "2026-11-10T17:51:00Z", price: 5.183 },
  ];
  const chart = lineChart({ asset: "USD-BRL", points, threshold: 5, direction: "BELOW", hours: 24, now });
  assert.match(chart, /stroke-dasharray="5 4"/);
  assert.match(chart, /avisa daqui para baixo/);
  assert.match(chart, /<path d="M[\d.]+ [\d.]+L[\d.]+ [\d.]+"/);
  assert.match(chart, /aria-label="USD-BRL: último preço 5,1830; linha do alerta em 5,0000"/);
  const empty = lineChart({ asset: "VALE3", points: [], threshold: 65, direction: "ABOVE", hours: 168, now });
  assert.match(empty, /Sem cotações neste período/);
  assert.match(empty, /avisa daqui para cima/);
});
