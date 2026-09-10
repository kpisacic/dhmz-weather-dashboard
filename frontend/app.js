(() => {
  "use strict";

  // Kept somewhat shorter than the backend's own cache TTLs (see
  // docker-compose.yml) so a fresh backend fetch shows up reasonably
  // promptly, without polling far more often than the data can actually change.
  const WEATHER_POLL_MS = 5 * 60 * 1000;
  const RADAR_POLL_MS = 3 * 60 * 1000;
  const FORECAST_STRIP_GAP_MS = 12 * 60 * 60 * 1000; // one icon per ~12h, like the original card

  const LOCALE = navigator.language && navigator.language.startsWith("hr") ? "hr" : "en";
  const STRINGS = {
    en: { temperature: "Temperature", precipitation: "Precipitations" },
    hr: { temperature: "Temperatura", precipitation: "Padaline" },
  }[LOCALE];

  const $ = (id) => document.getElementById(id);

  const els = {
    stationName: $("station-name"),
    updated: $("updated"),
    conditionIcon: $("condition-icon"),
    temperature: $("temperature"),
    conditionText: $("condition-text"),
    humidity: $("humidity"),
    precipitation: $("precipitation"),
    pressure: $("pressure"),
    pressureTendency: $("pressure-tendency"),
    sunRow: $("sun-row"),
    sunrise: $("sunrise"),
    sunset: $("sunset"),
    windArrow: $("wind-arrow"),
    windBearing: $("wind-bearing"),
    windSpeed: $("wind-speed"),
    todayBlock: $("today-block"),
    forecastToday: $("forecast-today"),
    tomorrowBlock: $("tomorrow-block"),
    forecastTomorrow: $("forecast-tomorrow"),
    forecastStrip: $("forecast-strip"),
    radarImage: $("radar-image"),
    status: $("status"),
  };

  let chart = null;

  function parseDhmzTimestamp(str) {
    // DHMZ's own timestamps are "DD.MM.YYYY HH:MM:SS", not ISO-8601.
    if (!str) return null;
    const m = str.match(/^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})$/);
    if (!m) return new Date(str);
    const [day, month, year, hour, min, sec] = m.slice(1).map(Number);
    return new Date(year, month - 1, day, hour, min, sec);
  }

  function fmtTime(isoString) {
    if (!isoString) return "--:--";
    const d = new Date(isoString);
    return d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function fmtNumber(value, digits = 1) {
    return typeof value === "number" ? Number(value.toFixed(digits)) : "--";
  }

  function setText(el, value) {
    el.textContent = value === null || value === undefined || value === "" ? "" : value;
  }

  function localIconUrl(symbol) {
    // Same-origin, proxied+cached by the backend (see /api/icon/<symbol> in
    // main.py) instead of pointing straight at meteo.hr - some Android
    // kiosk browsers/WebViews whitelist only their own configured origin
    // and silently block third-party image requests.
    if (!symbol || symbol === "-") return null;
    return `/api/icon/${encodeURIComponent(symbol)}`;
  }

  function render(data) {
    setText(els.stationName, data.station || data.station_name || "DHMZ");
    const updatedDate = parseDhmzTimestamp(data.updated);
    if (updatedDate) {
      els.updated.textContent = "Updated " + updatedDate.toLocaleString(LOCALE, {
        weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
      });
    }

    const conditionIconSrc = localIconUrl(data.weather_symbol);
    if (conditionIconSrc) {
      els.conditionIcon.src = conditionIconSrc;
      els.conditionIcon.style.visibility = "visible";
    } else {
      els.conditionIcon.style.visibility = "hidden";
    }
    setText(els.temperature, fmtNumber(data.temperature));
    setText(els.conditionText, data.condition_text || "");

    setText(els.humidity, fmtNumber(data.humidity, 0));
    setText(els.precipitation, fmtNumber(data.precipitation));
    setText(els.pressure, fmtNumber(data.pressure));
    if (data.pressure_tendency !== null && data.pressure_tendency !== undefined) {
      const sign = data.pressure_tendency > 0 ? "+" : "";
      els.pressureTendency.textContent = `(${sign}${fmtNumber(data.pressure_tendency)})`;
    } else {
      els.pressureTendency.textContent = "";
    }

    if (data.sun && data.sun.sunrise && data.sun.sunset) {
      els.sunRow.hidden = false;
      setText(els.sunrise, fmtTime(data.sun.sunrise));
      setText(els.sunset, fmtTime(data.sun.sunset));
    } else {
      els.sunRow.hidden = true;
    }

    if (data.wind_bearing_deg !== null && data.wind_bearing_deg !== undefined) {
      els.windArrow.style.visibility = "visible";
      els.windArrow.style.transform = `rotate(${data.wind_bearing_deg + 180}deg)`;
    } else {
      els.windArrow.style.visibility = "hidden";
    }
    setText(els.windBearing, data.wind_bearing_compass || "--");
    setText(els.windSpeed, fmtNumber(data.wind_speed));

    setText(els.forecastToday, data.forecast_today || "");
    els.todayBlock.style.display = data.forecast_today ? "" : "none";
    setText(els.forecastTomorrow, data.forecast_tomorrow || "");
    els.tomorrowBlock.style.display = data.forecast_tomorrow ? "" : "none";

    renderForecastStrip(data.forecast_list || []);
    renderChart(data.forecast_list || []);
  }

  function renderForecastStrip(forecastList) {
    els.forecastStrip.innerHTML = "";
    let lastTime = 0;
    for (const entry of forecastList) {
      const t = new Date(entry.datetime).getTime();
      if (t < lastTime) continue;
      lastTime = t + FORECAST_STRIP_GAP_MS;

      const item = document.createElement("div");
      item.className = "fc-item";
      const img = document.createElement("img");
      img.src = localIconUrl(entry.weather_symbol) || "";
      img.alt = entry.condition || "";
      const label = document.createElement("span");
      label.textContent = new Date(entry.datetime).toLocaleTimeString(LOCALE, {
        weekday: "short", hour: "2-digit", hour12: false,
      });
      item.appendChild(img);
      item.appendChild(label);
      els.forecastStrip.appendChild(item);
    }
  }

  function chartColors() {
    // Matches the fixed dark palette in style.css.
    return { text: "#b7bec9", divider: "rgba(255,255,255,0.12)" };
  }

  function renderChart(forecastList) {
    const canvas = $("weather-chart");
    if (!canvas || typeof Chart === "undefined" || forecastList.length === 0) return;

    const ctx = canvas.getContext("2d");
    const { text, divider } = chartColors();

    const labels = forecastList.map((d) => new Date(d.datetime));
    const temps = forecastList.map((d) => d.temperature);
    const precip = forecastList.map((d) => d.precipitation || 0);

    const gradient = ctx.createLinearGradient(0, 0, 0, 150);
    gradient.addColorStop(0, "rgba(255, 99, 132, 0.3)");
    gradient.addColorStop(1, "rgba(255, 99, 132, 0.0)");

    if (chart) {
      chart.destroy();
    }

    chart = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: STRINGS.temperature,
            type: "line",
            data: temps,
            borderColor: "#ff6384",
            backgroundColor: gradient,
            borderWidth: 2,
            tension: 0.4,
            pointRadius: 1,
            pointHoverRadius: 5,
            fill: true,
            yAxisID: "yTemp",
          },
          {
            label: STRINGS.precipitation,
            type: "bar",
            data: precip,
            backgroundColor: "rgba(54, 162, 235, 0.6)",
            borderColor: "#36a2eb",
            borderWidth: 1,
            borderRadius: 4,
            yAxisID: "yPrecip",
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "rgba(0,0,0,0.8)",
            titleColor: "#fff",
            bodyColor: "#fff",
            borderColor: divider,
            borderWidth: 1,
            padding: 12,
            callbacks: {
              title: (items) => new Date(items[0].parsed.x).toLocaleTimeString(LOCALE, {
                weekday: "short", hour: "numeric", hour12: false,
              }),
              label: (item) => {
                const unit = item.datasetIndex === 0 ? "°C" : "mm";
                return `${item.dataset.label}: ${item.parsed.y} ${unit}`;
              },
            },
          },
        },
        scales: {
          x: {
            type: "time",
            position: "top",
            time: { unit: "hour", displayFormats: { hour: "ccc HH:mm" } },
            grid: { color: divider },
            ticks: { color: text, autoSkip: true, maxRotation: 0 },
          },
          yTemp: {
            position: "left",
            grid: { color: divider, borderDash: [1, 3] },
            ticks: { color: text },
          },
          yPrecip: {
            display: false,
            position: "right",
            suggestedMax: 10,
            ticks: { min: 0 },
          },
        },
      },
    });
  }

  function setStatus(message, isStale) {
    els.status.textContent = message;
    els.status.classList.toggle("stale", Boolean(isStale));
  }

  async function pollWeather() {
    try {
      const res = await fetch("/api/weather", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      render(data);
      setStatus("", false);
    } catch (err) {
      console.error("Failed to fetch weather:", err);
      setStatus("Data may be stale — refresh failed", true);
    }
  }

  function pollRadar() {
    els.radarImage.src = `/api/radar?t=${Date.now()}`;
  }

  pollWeather();
  pollRadar();
  setInterval(pollWeather, WEATHER_POLL_MS);
  setInterval(pollRadar, RADAR_POLL_MS);
})();
