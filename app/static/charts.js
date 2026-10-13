/* Line chart for member history: biomarkers now, wearables next.
 *
 *   lineChart(canvas, { points, unit, periods })
 *
 * points   [{ label, value, status? }]  value may be null: the line breaks there.
 * unit     shown on the y axis and in the tooltip.
 * periods  optional [{ startIndex, ranges }]: reference ranges that apply from
 *          points[startIndex] until the next period. Drawn as translucent bands.
 *
 * The x axis is a category axis of preformatted labels, so no date adapter.
 */

(function () {
  const BAND_LABEL = { optimal: "Optimal", good: "Good", improve: "Needs work" };
  const BAND_ALPHA = 0.35;

  function cssColor(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(`--${name}`)
      .trim();
  }

  function withAlpha(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  /* One set of band rectangles per period, across the x-slices of the points it
     covers. The slice first gets "improve", then each band clears and paints its
     own strip, so anything no band covers stays red, matching band_for() on the
     server, and translucent colours never stack. */
  function bandsPlugin(periods) {
    return {
      id: "referenceBands",
      beforeDatasetsDraw(chart) {
        if (!periods.length) return;
        const { ctx, chartArea: area, scales } = chart;
        const count = chart.data.labels.length;
        const fill = Object.fromEntries(
          ["optimal", "good", "improve"].map((b) => [b, withAlpha(cssColor(b), BAND_ALPHA)])
        );
        const edge = (i) =>
          i <= 0
            ? area.left
            : i >= count
              ? area.right
              : (scales.x.getPixelForValue(i - 1) + scales.x.getPixelForValue(i)) / 2;

        ctx.save();
        ctx.beginPath();
        ctx.rect(area.left, area.top, area.width, area.height);
        ctx.clip();
        periods.forEach((period, p) => {
          const left = edge(period.startIndex);
          const right = edge(periods[p + 1]?.startIndex ?? count);
          ctx.fillStyle = fill.improve;
          ctx.fillRect(left, area.top, right - left, area.height);
          for (const [band, { min, max }] of Object.entries(period.ranges)) {
            const top = scales.y.getPixelForValue(max);
            const bottom = scales.y.getPixelForValue(min);
            ctx.clearRect(left, top, right - left, bottom - top);
            ctx.fillStyle = fill[band];
            ctx.fillRect(left, top, right - left, bottom - top);
          }
        });
        ctx.restore();
      },
    };
  }

  /* Fit the data, widened to the nearest band edge on each side so the member
     can see which band a point is near. Not the full band span: that can be
     0–400 and would flatten the line. */
  function yBounds(values, periods) {
    const present = values.filter((v) => v != null);
    if (!present.length) return {};
    let lo = Math.min(...present);
    let hi = Math.max(...present);
    const edges = periods.flatMap((p) =>
      Object.values(p.ranges).flatMap((b) => [b.min, b.max])
    );
    const below = edges.filter((e) => e <= lo);
    const above = edges.filter((e) => e >= hi);
    if (below.length) lo = Math.max(...below);
    if (above.length) hi = Math.min(...above);
    const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1;
    return { min: lo >= 0 ? Math.max(0, lo - pad) : lo - pad, max: hi + pad };
  }

  /* Key for the background bands. Only charts drawn with periods need one. */
  window.bandLegend = function () {
    const list = document.createElement("ul");
    list.className = "band-legend";
    for (const band of ["optimal", "good", "improve"]) {
      const item = document.createElement("li");
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = withAlpha(cssColor(band), BAND_ALPHA * 1.6);
      item.append(swatch, BAND_LABEL[band]);
      list.append(item);
    }
    return list;
  };

  window.lineChart = function (canvas, { points, unit, periods = [] }) {
    const ink = cssColor("ink");
    const pointColor = (p) => (p.status ? cssColor(p.status) : ink);
    const values = points.map((p) => p.value);

    return new Chart(canvas, {
      type: "line",
      data: {
        labels: points.map((p) => p.label),
        datasets: [
          {
            label: unit,
            data: values,
            borderColor: ink,
            borderWidth: 1.5,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: points.map(pointColor),
            pointBorderColor: points.map(pointColor),
            spanGaps: false,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { ...yBounds(values, periods), title: { display: true, text: unit } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(item) {
                const p = points[item.dataIndex];
                const band = BAND_LABEL[p.status];
                return `${p.value.toLocaleString()} ${unit}${band ? ` · ${band}` : ""}`;
              },
            },
          },
        },
      },
      plugins: [bandsPlugin(periods)],
    });
  };
})();
