/* Shared Chart.js helpers. Data comes from server-rendered json_script blocks. */
window.HRCharts = {
  balance: function (canvasId, dataId) {
    const el = document.getElementById(canvasId);
    const src = document.getElementById(dataId);
    if (!el || !src || !window.Chart) return;
    const data = JSON.parse(src.textContent);
    if (!data.length) return;
    new Chart(el, {
      type: "bar",
      data: {
        labels: data.map(function (d) { return d.label; }),
        datasets: [
          { label: "Used", data: data.map(function (d) { return d.used; }), backgroundColor: "#94a3b8", borderRadius: 4 },
          { label: "Pending", data: data.map(function (d) { return d.pending; }), backgroundColor: "#e0a106", borderRadius: 4 },
          { label: "Available", data: data.map(function (d) { return d.available; }), backgroundColor: "#1f5eff", borderRadius: 4 }
        ]
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { family: "Inter" } } } },
        scales: { x: { stacked: true, beginAtZero: true, grid: { color: "#f0f2f6" } }, y: { stacked: true, grid: { display: false } } }
      }
    });
  }
};
