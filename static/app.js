const $ = (sel, root = document) => root.querySelector(sel);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const money = (n) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (n, digits = 2) => (n >= 0 ? "+" : "−") + Math.abs(n).toFixed(digits);
const pct = (n, digits = 2) => signed(n, digits) + "%";
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const tone = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
const change = (price, base) => (price / base - 1) * 100;
const day = (iso, opts = { weekday: "short", month: "short", day: "numeric" }) =>
  new Date(iso + "T12:00:00").toLocaleDateString("en-US", opts);
const longDay = (iso) => day(iso, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
const ordinal = (n) => {
  const suffix = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (suffix[(v - 20) % 10] || suffix[v] || suffix[0]);
};
// Places 1–5 score points (green); 6th or worse doesn't (red).
const placeTone = (place) => (place <= 5 ? "up" : "down");
const wlt = ([w, l, t]) => `${w}–${l}${t ? `–${t}` : ""}`;
const YOU = "You";
const HUMANITY = "Humanity";
const BASELINE = "No Change (baseline)";

let state = null;
let quarterShown = null;
let practiceQ = null;
let practiceResult = null;
let callKey = null;
const charts = {};

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: res.statusText }));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* ---------- Tabs ---------- */

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

function showTab(name) {
  if (!$(`#tab-${name}`)) name = "live";
  const url = new URL(location.href);
  url.hash = name === "live" ? "" : name;
  history.replaceState(null, "", url);
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
  if (name === "practice" && !practiceQ && !practiceResult) newPractice();
}

/* ---------- Price chart ---------- */

const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements();
    if (!active?.length) return;
    const { ctx, chartArea } = chart;
    ctx.save();
    ctx.strokeStyle = css("--axis");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(active[0].element.x, chartArea.top);
    ctx.lineTo(active[0].element.x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

function priceChart(id, labels, closes, { highlightLast = false, tooltipTitle, guess } = {}) {
  charts[id]?.destroy();
  const last = closes.length - 1;
  const guessMarker = guess == null ? [] : [{
    label: "Your guess",
    order: -1,  // draw on top of the close line's end dot when they overlap
    data: closes.map((_, i) => (i === last ? guess : null)),
    showLine: false,
    pointRadius: 6,
    pointHoverRadius: 6,
    pointBackgroundColor: css("--you"),
    pointBorderColor: css("--surface"),
    pointBorderWidth: 2,
  }];
  charts[id] = new Chart($(`#${id}`), {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: closes,
        borderColor: css("--model"),
        backgroundColor: css("--model"),
        borderWidth: 2,
        borderJoinStyle: "round",
        borderCapStyle: "round",
        pointRadius: closes.map((_, i) => (highlightLast && i === last ? 5 : 0)),
        pointBorderColor: css("--surface"),
        pointBorderWidth: 2,
        pointHoverRadius: 5,
        tension: 0,
      }, ...guessMarker],
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          filter: (item) => item.parsed.y != null,
          callbacks: {
            title: (items) => (tooltipTitle ? tooltipTitle(items[0].dataIndex) : longDay(labels[items[0].dataIndex])),
            label: (item) => (item.dataset.label ? `${item.dataset.label}: ` : "") + money(item.parsed.y),
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: css("--axis") },
          ticks: {
            color: css("--muted"), maxRotation: 0, autoSkip: true, maxTicksLimit: 6,
            callback: (i) => (tooltipTitle ? "" : day(labels[i], { month: "short", day: "numeric" })),
          },
        },
        y: {
          grid: { color: css("--grid") },
          border: { display: false },
          ticks: { color: css("--muted"), maxTicksLimit: 5, callback: (v) => v.toLocaleString("en-US") },
        },
      },
    },
    plugins: [crosshair],
  });
}

/* ---------- Forecast strip ---------- */

function forecastStrip({ base, predictions, guess, crowd, actual, scores }) {
  const rows = Object.entries(predictions).map(([name, price]) => ({ name, price }));
  if (guess != null) rows.push({ name: YOU, price: guess });
  if (crowd) rows.push({ name: HUMANITY, price: crowd.median });
  rows.forEach((r) => (r.chg = change(r.price, base)));
  rows.sort((a, b) => b.chg - a.chg);

  const actualChg = actual != null ? change(actual, base) : null;
  const extent = 1.15 * Math.max(0.25, ...rows.map((r) => Math.abs(r.chg)), Math.abs(actualChg ?? 0));
  const x = (c) => `${50 + (c / extent) * 50}%`;
  const actualLine = actualChg != null ? `<span class="strip-actual" style="left:${x(actualChg)}"></span>` : "";

  const body = rows.map((r) => {
    const s = scores?.[r.name];
    const cls = { [YOU]: "you", [HUMANITY]: "humanity", [BASELINE]: "baseline" }[r.name] || "";
    const detail = s ? `missed by ${money(s.err)} · ${s.points} pts` : pct(r.chg);
    return `<div class="strip-row ${cls}">
      <span class="strip-name">${r.name}</span>
      <span class="strip-track"><span class="strip-zero" style="left:50%"></span>${actualLine}
        <span class="strip-dot" style="left:${x(r.chg)}" title="${r.name}: ${money(r.price)} (${pct(r.chg)})"></span></span>
      <span class="strip-val">${money(r.price)} <small>${detail}</small></span>
    </div>`;
  }).join("");

  return `<div class="table-wrap"><div class="strip">
      <span></span>
      <span class="strip-axis"><span style="left:0">${pct(-extent)}</span><span style="left:50%">no change</span><span style="left:100%">${pct(extent)}</span></span>
      <span></span>
      ${body}
    </div></div>
    <div class="legend">
      <span><i class="key" style="background:var(--model)"></i>Model forecast</span>
      ${guess != null ? `<span><i class="key" style="background:var(--you)"></i>Your guess</span>` : ""}
      ${crowd ? `<span><i class="key" style="background:var(--humanity)"></i>Humanity (median of ${plural(crowd.count, "guess", "guesses")})</span>` : ""}
      ${actualChg != null ? `<span><i class="key line" style="background:var(--ink)"></i>Actual close (${pct(actualChg)})</span>` : ""}
    </div>`;
}

/* ---------- Tables ---------- */

function table(columns, rows) {
  const head = columns.map((c) => `<th class="${c.cls || ""}">${c.label}</th>`).join("");
  const body = rows.map((r, i) => `<tr>${columns.map((c) =>
    `<td class="${c.cls || ""}">${c.value(r, i)}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ---------- Guess form (shared by live and practice) ---------- */

function guessForm(buttonLabel) {
  return `<form class="guess" novalidate>
    <label class="field">Your price
      <input type="number" step="0.01" inputmode="decimal" required>
    </label>
    <div class="implied"></div>
    <input type="range" min="-3" max="3" step="0.01" value="0" aria-label="Percent change from last close">
    <div class="ticks"><span>−3%</span><span>no change</span><span>+3%</span></div>
    <button class="primary" type="submit">${buttonLabel}</button>
    <p class="error" role="alert"></p>
  </form>`;
}

function bindGuess(root, base, submit) {
  const form = $("form", root);
  const num = $('input[type="number"]', form);
  const range = $('input[type="range"]', form);
  const implied = $(".implied", form);
  const button = $("button", form);
  const error = $(".error", form);

  const show = () => {
    const v = parseFloat(num.value);
    implied.innerHTML = Number.isFinite(v)
      ? `<span class="${tone(+change(v, base).toFixed(2))}">${pct(change(v, base))}</span> from the last close of ${money(base)}`
      : "";
  };
  num.value = base.toFixed(2);
  range.addEventListener("input", () => { num.value = (base * (1 + range.value / 100)).toFixed(2); show(); });
  num.addEventListener("input", () => {
    const v = parseFloat(num.value);
    if (Number.isFinite(v)) range.value = change(v, base).toFixed(2);
    show();
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    button.disabled = true;
    try {
      await submit(parseFloat(num.value));
    } catch (err) {
      error.textContent = err.message;
      button.disabled = false;
    }
  });
  show();
}

/* ---------- Live game ---------- */

function countdown(lockAt) {
  const ms = new Date(lockAt) - Date.now();
  if (ms <= 0) return "Guessing is closed.";
  const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4);
  const left = h >= 48 ? `${Math.floor(h / 24)}d ${h % 24}h` : h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
  return `Guessing closes at the 9:30 AM ET open, in ${left}.`;
}

function renderLive(s) {
  const dayChange = s.last_close - s.prev_close;
  $("#last-close").textContent = money(s.last_close);
  $("#last-change").innerHTML = `<span class="${tone(dayChange)}">${signed(dayChange)} (${pct(change(s.last_close, s.prev_close))})</span>`;
  $("#as-of").textContent = `Close on ${longDay(s.as_of)}`;
  priceChart("live-chart", s.history.map((p) => p.date), s.history.map((p) => p.close));

  const target = day(s.target_date);
  const call = $("#call");
  // Only rebuild the call card when the round changes, so a refresh can't wipe a half-typed guess.
  const key = `${s.as_of}|${s.guess}|${s.open}`;
  if (key === callKey) {
    // unchanged
  } else if (s.guess != null) {
    const chg = change(s.guess, s.last_close);
    call.innerHTML = `<h2>Your call</h2>
      <p class="lede">You're locked in for the close on <b>${target}</b>.</p>
      <div class="locked"><span class="locked-value">${money(s.guess)}</span><span class="${tone(chg)}">${pct(chg)}</span></div>
      <p class="muted" id="crowd-line"></p>
      <p class="notice">Results post after the 4:00 PM ET close on ${target}. Come back then to see who won.
        Can't wait? Warm up in <a href="#" data-goto="practice">Practice</a>.</p>`;
  } else if (!s.open) {
    call.innerHTML = `<h2>Your call</h2>
      <p class="notice">The ${target} session has already opened, so guessing for it is closed.
        A new round opens after today's 4:00 PM ET close. Meanwhile, try <a href="#" data-goto="practice">Practice</a>.</p>
      <p class="muted" id="crowd-line"></p>`;
  } else {
    call.innerHTML = `<h2>Your call</h2>
      <p class="lede">Where will the S&amp;P 500 close on <b>${target}</b>?</p>
      ${guessForm("Lock it in")}
      <p class="muted" id="countdown">${countdown(s.lock_at)}</p>
      <p class="muted" id="crowd-line"></p>`;
    bindGuess(call, s.last_close, async (value) => render(await api("/api/guess", { guess: value })));
  }
  callKey = key;

  // The guess count changes between refreshes, so it lives outside the rebuild check above.
  const { count, median } = s.crowd;
  $("#crowd-line", call).innerHTML = median != null
    ? `Humanity's median: <b>${money(median)}</b> <span class="${tone(median - s.last_close)}">${pct(change(median, s.last_close))}</span>
       from ${plural(count, "guess", "guesses")}.`
    : !count ? (s.open ? "No guesses yet. Be the first." : "Nobody guessed this round.")
    : `${plural(count, "guess", "guesses")} so far. You'll see their median once you lock in yours.`;

  $("#forecast-title").textContent = `Forecasts for the ${target} close`;
  $("#forecasts").innerHTML = s.predictions
    ? forecastStrip({ base: s.last_close, predictions: s.predictions, guess: s.guess,
        crowd: s.crowd.median != null ? s.crowd : null })
    : `<p class="notice">The models have made their picks. Lock in your guess to see them, so they can't sway you.</p>`;

  const entrant = (r, who) => (r.places[who] == null ? "—"
    : `${money(r.scores[who].pred)} <small class="${placeTone(r.places[who])}">${ordinal(r.places[who])}</small>`);
  $("#history").innerHTML = s.rounds.length ? table([
    { label: "Close on", value: (r) => day(r.target_date, { month: "short", day: "numeric", year: "numeric" }) },
    { label: "Actual", value: (r) => `${money(r.actual)} <span class="${tone(r.actual - r.base)}">${pct(change(r.actual, r.base))}</span>` },
    { label: "Humanity", value: (r) => entrant(r, HUMANITY) },
    { label: "Guesses", value: (r) => r.guesses },
    { label: "You", value: (r) => entrant(r, YOU) },
    { label: "Best bot", cls: "left", value: (r) => `${r.best_model} (${money(r.scores[r.best_model].err)})` },
    { label: "Winner", cls: "left", value: (r) => r.winners.join(", ") },
  ], s.rounds) : `<p class="empty">Finished rounds show up here after each close.</p>`;
}

/* ---------- Last round ---------- */

function record(h2h, human) {
  // A human's total [wins, losses, ties] across every bot.
  const total = [0, 0, 0];
  Object.values(h2h || {}).forEach((bot) => bot[human]?.forEach((n, i) => (total[i] += n)));
  return total;
}

function renderLastRound(s) {
  const card = $("#last-round");
  const r = s.rounds[0];
  card.hidden = !r;
  if (!r) return;
  const chg = change(r.actual, r.base);
  const bots = Object.keys(r.scores).filter((p) => p !== HUMANITY && p !== YOU);

  const tile = (who, label, color) => {
    const place = r.places[who];
    if (place == null) {
      return `<div class="stat"><span class="stat-label">${label}</span><span class="muted">You didn't guess this round.</span></div>`;
    }
    const beat = bots.filter((b) => r.places[b] > place).length;
    return `<div class="stat"><span class="stat-label"><i class="key" style="background:var(${color})"></i>${label}</span>
      <span class="stat-value">${money(r.scores[who].pred)}</span>
      <small class="${placeTone(place)}">${ordinal(place)} place · ${r.points[who]} pts · beat ${beat} of ${bots.length} bots</small></div>`;
  };

  const sb = s.scoreboard;
  const quarterH2h = sb.head_to_head[sb.current];
  const records = [[HUMANITY, record(quarterH2h, HUMANITY)], [YOU, record(quarterH2h, YOU)]]
    .filter(([, rec]) => rec.some((n) => n))
    .map(([who, rec]) => `${who} <b class="${tone(rec[0] - rec[1])}">${wlt(rec)}</b>`);

  card.innerHTML = `<h2>Last round · ${longDay(r.target_date)} close</h2>
    <p class="lede">The S&amp;P 500 closed at <b>${money(r.actual)}</b> (<span class="${tone(chg)}">${pct(chg)}</span>).</p>
    ${r.league === "main"
      ? `<div class="stats">
          ${tile(HUMANITY, `Humanity · median of ${plural(r.guesses, "guess", "guesses")}`, "--humanity")}
          ${tile(YOU, "You", "--you")}
        </div>`
      : `<p class="notice">Nobody guessed this round, so it only counted in the bot league.
          Best bot: ${r.best_model}, off by ${money(r.scores[r.best_model].err)}.</p>`}
    ${records.length ? `<p class="muted">Record vs the bots in ${sb.current}: ${records.join(" · ")}.
      <a href="#" data-goto="scoreboard">See the scoreboard</a></p>` : ""}`;
}

/* ---------- Practice ---------- */

async function newPractice() {
  practiceResult = null;
  const el = $("#practice");
  el.innerHTML = `<p class="muted">Picking a mystery day…</p>`;
  try {
    practiceQ = await api("/api/practice");
  } catch (err) {
    el.innerHTML = `<p class="notice">${err.message}</p>
      <div class="row-actions"><button class="secondary" id="retry-practice">Try again</button></div>`;
    $("#retry-practice").addEventListener("click", newPractice);
    return;
  }
  renderPracticeQuestion(practiceQ);
}

function renderPracticeQuestion(q) {
  const el = $("#practice");
  const n = q.history.length;
  el.innerHTML = `<h2>Mystery day</h2>
    <p class="lede">Here are 60 trading days of the S&amp;P 500 from somewhere in the last two years, a stretch the models
      never trained on. The date stays hidden.
      The last close was <b>${money(q.base_close)}</b>. Where did it close the next day?</p>
    <div class="grid">
      <div class="chart"><canvas id="practice-chart" aria-label="Mystery 60-day price chart"></canvas></div>
      <div>${guessForm("Reveal")}</div>
    </div>`;
  priceChart("practice-chart", q.history.map((_, i) => i), q.history, {
    highlightLast: true, tooltipTitle: (i) => (i === n - 1 ? "Mystery day" : `${n - 1 - i} days earlier`),
  });
  bindGuess(el, q.base_close, async (value) => {
    const res = await api("/api/practice", { q: q.q, guess: value });
    practiceQ = null;
    practiceResult = res;
    renderPracticeResult(res);
  });
}

function renderPracticeResult({ result: r, history }) {
  const you = r.scores[YOU];
  const rank = 1 + Object.values(r.scores).filter((s) => s.err < you.err).length;
  const players = Object.keys(r.scores).length;
  const actualChg = change(r.actual, r.base);
  const predictions = Object.fromEntries(Object.entries(r.scores).filter(([p]) => p !== YOU).map(([p, s]) => [p, s.pred]));
  const guessChg = change(you.pred, r.base);
  $("#practice").innerHTML = `<h2>The reveal</h2>
    <p class="place ${placeTone(rank)}">
      ${r.winners.includes(YOU) ? `You won this round! 1st of ${players}.` : `You placed ${ordinal(rank)} of ${players}.`}</p>
    <div class="stats">
      <div class="stat"><span class="stat-label"><i class="key" style="background:var(--you)"></i>Your guess</span>
        <span class="stat-value">${money(you.pred)}</span><small class="${tone(guessChg)}">${pct(guessChg)}</small></div>
      <div class="stat"><span class="stat-label">Actual close</span>
        <span class="stat-value">${money(r.actual)}</span><small class="${tone(actualChg)}">${pct(actualChg)}</small></div>
      <div class="stat"><span class="stat-label">You missed by</span>
        <span class="stat-value">${money(you.err)}</span><small class="muted">${you.pct.toFixed(2)}% of the close</small></div>
      <div class="stat"><span class="stat-label">Points</span>
        <span class="stat-value">${you.points}</span><small class="muted">${you.direction ? "includes +20 for the direction" : "no direction bonus"}</small></div>
    </div>
    <p class="muted">The mystery day was ${longDay(r.base_date)}, so the answer was the close on ${longDay(r.target_date)}.
      Closest: ${r.winners.join(", ")}.</p>
    <div class="chart"><canvas id="practice-chart" aria-label="Price chart with the revealed close and your guess"></canvas></div>
    <div class="legend">
      <span><i class="key hline" style="background:var(--model)"></i>S&amp;P 500 close (the last dot is the revealed close)</span>
      <span><i class="key" style="background:var(--you)"></i>Your guess</span>
    </div>
    <h2 class="spaced">Forecasts vs the actual close</h2>
    ${forecastStrip({ base: r.base, predictions, guess: you.pred, actual: r.actual, scores: r.scores })}
    <div class="row-actions"><button class="primary" id="next-practice">Next mystery day</button></div>`;
  priceChart("practice-chart", history.map((p) => p.date), history.map((p) => p.close), { highlightLast: true, guess: you.pred });
  $("#next-practice").addEventListener("click", newPractice);
}

/* ---------- Accuracy & models ---------- */

const direction = (d) => (d == null ? "—" : d.toFixed(0) + "%");

function renderAccuracy(s) {
  $("#accuracy").innerHTML = s.accuracy.length ? table([
    { label: "Player", value: (r) => (r.player === HUMANITY ? "<b>Humanity (median guess)</b>"
      : r.player === YOU ? "<b>You</b>" : r.player) },
    { label: "Rounds", value: (r) => r.rounds },
    { label: "Avg miss", value: (r) => money(r.avg_err) },
    { label: "Avg miss %", value: (r) => r.avg_pct.toFixed(3) + "%" },
    { label: "Called direction", value: (r) => direction(r.direction) },
  ], s.accuracy) : `<p class="empty">No finished rounds yet. The first results arrive after the next 4:00 PM ET close.
    Until then, the backtest below shows how each model did over the last two years.</p>`;

  const bt = s.backtest;
  if (!bt) {
    $("#backtest-intro").textContent = "Still running: the models are being retrained month by month through the last two years. This takes two or three minutes after the server starts.";
    $("#backtest").innerHTML = "";
    return;
  }
  const trained = bt.train_years ? `the ${bt.train_years} years` : "all the data";
  $("#backtest-intro").textContent = `Each model forecast every close from ${longDay(bt.start)} to ${longDay(bt.end)}
    (${bt.days} trading days). At the start of each month it was retrained on ${trained} before that month, so it never
    saw the days it was forecasting. "vs No Change" is how much smaller the average miss is than just guessing
    yesterday's close. Positive means the model beat it.`;
  $("#backtest").innerHTML = table([
    { label: "Model", value: (r) => r.model },
    { label: "Avg miss", value: (r) => money(r.mae) },
    { label: "Avg miss %", value: (r) => r.mape.toFixed(3) + "%" },
    { label: "Called direction", value: (r) => direction(r.direction) },
    { label: "vs No Change", value: (r) => `<span class="${tone(r.vs_baseline)}">${pct(r.vs_baseline)}</span>` },
  ], bt.models);
}

function renderScoreboard(s) {
  const sb = s.scoreboard;
  if (!sb.quarters.includes(quarterShown)) quarterShown = sb.current;
  $("#quarter").innerHTML = sb.quarters.map((q) => `<option ${q === quarterShown ? "selected" : ""}>${q}</option>`).join("");
  $("#scoreboard-title").textContent = `Quarterly scoreboard · ${quarterShown}`;

  const name = (p) => (p === HUMANITY || p === YOU ? `<b>${p}</b>` : p);
  const standings = (rows, empty) => (rows.length ? table([
    { label: "#", value: (_, i) => i + 1 },
    { label: "Entrant", cls: "left", value: (r) => name(r.entrant) },
    { label: "Points", value: (r) => r.points },
    { label: "1st places", value: (r) => r.firsts },
    { label: "Days", value: (r) => r.days },
  ], rows) : `<p class="empty">${empty}</p>`);
  const league = sb.leagues[quarterShown];
  $("#main-league").innerHTML = standings(league.main, "Nobody has guessed yet this quarter.");
  $("#bot-league").innerHTML = standings(league.bots, "No finished rounds yet this quarter.");

  const quarter = sb.head_to_head[quarterShown] || {};
  const all = sb.head_to_head.all || {};
  const bots = s.models.map((m) => m.model).filter((b) => all[b]);
  const cell = (rec, bold) => (rec ? `<span class="${tone(rec[0] - rec[1])}">${bold ? `<b>${wlt(rec)}</b>` : wlt(rec)}</span>` : "—");
  const rows = bots.map((b) => ({ bot: b, hq: quarter[b]?.[HUMANITY], yq: quarter[b]?.[YOU], ha: all[b][HUMANITY], ya: all[b][YOU] }));
  const sum = (h2h, who) => { const t = record(h2h, who); return t.some((n) => n) ? t : undefined; };
  rows.push({ bot: "<b>All bots</b>", total: true, hq: sum(quarter, HUMANITY), yq: sum(quarter, YOU), ha: sum(all, HUMANITY), ya: sum(all, YOU) });
  $("#h2h").innerHTML = bots.length ? table([
    { label: "Bot", value: (r) => r.bot },
    { label: `Humanity · ${quarterShown}`, value: (r) => cell(r.hq, r.total) },
    { label: `You · ${quarterShown}`, value: (r) => cell(r.yq, r.total) },
    { label: "Humanity · all time", value: (r) => cell(r.ha, r.total) },
    { label: "You · all time", value: (r) => cell(r.ya, r.total) },
  ], rows) : `<p class="empty">No head-to-head results yet. They start with the first finished round someone guessed.</p>`;
}

$("#quarter").addEventListener("change", (e) => {
  quarterShown = e.target.value;
  renderScoreboard(state);
});

function renderModels(s) {
  $("#model-list").innerHTML = table([
    { label: "Model", value: (r) => r.model },
    { label: "How it works", cls: "about", value: (r) => r.about },
  ], s.models);
}

/* ---------- Boot ---------- */

function render(s) {
  state = s;
  $("#loading").hidden = true;
  if (document.querySelectorAll(".tab:not([hidden])").length === 0) showTab(location.hash.slice(1));
  renderLastRound(s);
  renderLive(s);
  renderScoreboard(s);
  renderAccuracy(s);
  renderModels(s);
  if (!s.backtest) setTimeout(refresh, 20_000);
}

async function refresh() {
  try {
    render(await api("/api/state"));
  } catch (err) {
    $("#loading").hidden = false;
    $("#loading").textContent = `Couldn't load market data: ${err.message}`;
  }
}

document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-goto]");
  if (link) { e.preventDefault(); showTab(link.dataset.goto); }
});

// Keep the countdown ticking; when the open passes or a new close lands, pull fresh state.
setInterval(() => {
  const el = $("#countdown");
  if (el && state) {
    el.textContent = countdown(state.lock_at);
    if (new Date(state.lock_at) <= Date.now()) refresh();
  }
}, 30_000);
setInterval(refresh, 10 * 60_000);

// Charts read colors from CSS variables, so redraw when the OS theme flips.
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (state) renderLive(state);
  if (practiceResult) renderPracticeResult(practiceResult);
  else if (practiceQ) renderPracticeQuestion(practiceQ);
});

refresh();
