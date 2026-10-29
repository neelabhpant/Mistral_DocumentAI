/* The confidence chart: average and lowest-word OCR confidence per page, as an inline SVG line chart (0–100 %). */

import {h, pct, svg} from "./dom.js";

const W = 640, H = 250, PAD = {left: 46, right: 16, top: 16, bottom: 46};
const SERIES = [
  {key: "avg_confidence", label: "Average", className: "series-average"},
  {key: "min_confidence", label: "Lowest word", className: "series-lowest"},
];

/** pages: [{number, avg_confidence, min_confidence}]. */
export function confidenceChart(pages) {
  const plotW = W - PAD.left - PAD.right, plotH = H - PAD.top - PAD.bottom;
  const x = i => PAD.left + (pages.length === 1 ? plotW / 2 : (i * plotW) / (pages.length - 1));
  const y = v => PAD.top + (1 - v) * plotH;
  const chart = svg("svg", {class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "OCR confidence per page"});

  for (const tick of [0, 0.25, 0.5, 0.75, 1]) {
    chart.append(svg("line", {class: "grid", x1: PAD.left, x2: W - PAD.right, y1: y(tick), y2: y(tick)}),
      svg("text", {class: "axis", x: PAD.left - 8, y: y(tick), "text-anchor": "end", "dominant-baseline": "middle"},
        `${tick * 100}%`));
  }
  pages.forEach((p, i) => chart.append(svg("text", {class: "axis", x: x(i), y: H - 26, "text-anchor": "middle"},
    String(p.number))));
  chart.append(svg("text", {class: "axis", x: PAD.left + plotW / 2, y: H - 6, "text-anchor": "middle"}, "Page"));

  for (const s of SERIES) {
    const points = pages.map((p, i) => [i, p[s.key]]).filter(([, v]) => v !== null && v !== undefined);
    const g = svg("g", {class: s.className});
    if (points.length > 1) {
      g.append(svg("polyline", {class: "line", points: points.map(([i, v]) => `${x(i)},${y(v)}`).join(" ")}));
    }
    for (const [i, v] of points) {
      g.append(svg("circle", {class: "point", cx: x(i), cy: y(v), r: 5},
        svg("title", {}, `Page ${pages[i].number} · ${s.label} · ${pct(v)}`)));
    }
    chart.append(g);
  }
  const legend = h("ul", {class: "legend chart-legend"},
    SERIES.map(s => h("li", {class: ["chip", s.className]}, h("span", {class: "swatch", "aria-hidden": "true"}), s.label)));
  return h("div", {class: "chart-wrap"}, legend, chart);
}
