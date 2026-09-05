/* Stock Trading Tracker -- frontend. No framework, no build step. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = { hotlist: [], slots: 3, lastRec: null };

/* ------------------------------------------------------------------ util */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}

let toastTimer;
function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("bad", bad);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 8000 : 3500);
}

const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
  ? "&mdash;" : Number(v).toLocaleString(undefined,
      { minimumFractionDigits: d, maximumFractionDigits: d });

/** Signed percentage with a colour class. Sign is always shown, so the meaning
 *  survives for anyone who cannot separate the two colours. */
function pct(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '<span class="flat">&mdash;</span>';
  const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
  const sign = v > 0 ? "+" : "";
  return `<span class="${cls} mono">${sign}${num(v, d)}%</span>`;
}

const today = () => new Date().toISOString().slice(0, 10);

/* ------------------------------------------------------------------ tabs */

$$("#tabs button").forEach(b => b.onclick = () => {
  $$("#tabs button").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "trades") loadTrades();
  if (b.dataset.tab === "history") loadHistory();
  if (b.dataset.tab === "record") loadScoreboard();
});

/** Clear the server's price cache and redraw whatever is on screen.
 *
 *  Prices are cached for ten minutes so a page load does not re-hit Yahoo. This
 *  button exists for when a fresher quote is wanted sooner. It previously only
 *  cleared the cache and said so, which left nothing on screen changing and
 *  read as though something had been lost -- it now re-runs the active tab so
 *  the effect is visible, and the wording says plainly that only prices are
 *  affected. */
$("#refresh").onclick = async () => {
  const btn = $("#refresh");
  const label = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Refreshing';
  try {
    await api("/api/refresh", { method: "POST" });
    const active = $("#tabs button.active").dataset.tab;
    if (active === "trades") await loadTrades();
    else if (active === "history") await loadHistory();
    else if (active === "record") await loadScoreboard();
    // On the pick tab, refresh the quotes only. Re-running the analysis would
    // save a second identical recommendation to the log.
    else await refreshCardQuotes();
    toast("Fresh prices from Yahoo. Your trades, hotlist and saved "
          + "recommendations are untouched.");
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
};

/* --------------------------------------------------------------- hotlist */

async function loadHotlist() {
  const d = await api("/api/hotlist");
  state.hotlist = d.hotlist;
  state.slots = d.slots;
  buildHotlist();
  paintHotlist();
}

/** Build the slot inputs once. They are never rebuilt afterwards, so editing
 *  one never steals focus from another -- only the labels around them repaint. */
function buildHotlist() {
  const box = $("#hotlist");
  box.innerHTML = "";
  for (let slot = 1; slot <= state.slots; slot++) {
    const el = document.createElement("div");
    el.className = "slot";
    el.dataset.slotBox = slot;
    el.innerHTML = `
      <div class="slot-label">Slot ${slot}</div>
      <div class="row">
        <input type="text" placeholder="e.g. AAPL" spellcheck="false"
               autocomplete="off" data-slot="${slot}">
        <button class="tiny danger" data-clear="${slot}" title="Clear slot">&times;</button>
      </div>
      <div class="meta"></div>`;
    const h = state.hotlist.find(x => x.slot === slot);
    $("input", el).value = h ? h.ticker : "";
    box.appendChild(el);
  }

  $$("#hotlist input").forEach(i => {
    // `change` fires on blur only when the value actually differs, which is
    // exactly when a commit is warranted.
    i.addEventListener("change", commitHotlist);
    i.addEventListener("input", paintHotlist);
    i.addEventListener("keydown", e => { if (e.key === "Enter") i.blur(); });
  });
  $$("#hotlist [data-clear]").forEach(b => b.onclick = () => {
    $(`#hotlist input[data-slot="${b.dataset.clear}"]`).value = "";
    commitHotlist();
  });
}

/** What is typed in the boxes right now -- the source of truth for the user. */
const typedTickers = () =>
  $$("#hotlist input").map(i => i.value.trim().toUpperCase());

/** What the server currently has stored. */
const savedTickers = () => {
  const out = [];
  for (let slot = 1; slot <= state.slots; slot++) {
    const h = state.hotlist.find(x => x.slot === slot);
    out.push(h ? h.ticker : "");
  }
  return out;
};

const hotlistDirty = () =>
  typedTickers().join("|") !== savedTickers().join("|");

function paintHotlist() {
  const typed = typedTickers();
  $$("#hotlist input").forEach(i => {
    const slot = +i.dataset.slot;
    const h = state.hotlist.find(x => x.slot === slot);
    const box = $(`#hotlist [data-slot-box="${slot}"]`);
    const meta = $(".meta", box);
    const dirty = typed[slot - 1] !== (h ? h.ticker : "");
    box.classList.toggle("dirty", dirty);
    if (dirty) {
      meta.innerHTML = typed[slot - 1]
        ? `<span class="pending">not checked yet &mdash; applied when you analyse</span>`
        : `<span class="pending">will be cleared</span>`;
    } else {
      meta.innerHTML = h
        ? `<strong>${esc(h.name || h.ticker)}</strong>
           ${[h.exchange, h.currency].filter(Boolean).map(esc).join(" &middot; ")}`
        : `<span class="flat">empty</span>`;
    }
  });
  $("#hotlist-state").textContent = hotlistDirty() ? "unsaved changes" : "";
}

/** Push the typed tickers to the server. Returns true when the stored hotlist
 *  ends up matching what is on screen. */
async function commitHotlist() {
  if (!hotlistDirty()) return true;
  const before = savedTickers().join("|");
  $$("#hotlist input").forEach(i => { i.disabled = true; });
  try {
    const d = await api("/api/hotlist", {
      method: "PUT", body: { tickers: typedTickers() },
    });
    state.hotlist = d.hotlist;
    // A different set of candidates makes any recommendation on screen stale.
    if (savedTickers().join("|") !== before) {
      $("#result").hidden = true;
      state.lastRec = null;
    }
    return true;
  } catch (e) {
    toast(e.message, true);
    return false;
  } finally {
    $$("#hotlist input").forEach(i => { i.disabled = false; });
    paintHotlist();
  }
}

/* ----------------------------------------------------------- the analysis */

$("#run").onclick = async () => {
  const btn = $("#run");
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Analysing';
  try {
    // Analyse what is on screen, not what happens to be stored. Typing a ticker
    // and pressing this button is the obvious gesture; requiring a separate
    // "Set" click first meant the analysis silently ran on the old hotlist.
    if (!await commitHotlist()) return;
    const rec = await api("/api/recommend", {
      method: "POST", body: { save: $("#save-rec").checked },
    });
    state.lastRec = rec;
    // Needed before the chart draws, so the holding bands appear on the first
    // render rather than popping in afterwards.
    try {
      state.trades = (await api("/api/trades")).trades;
    } catch { state.trades = []; }
    renderRecommendation(rec);
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyse & recommend";
  }
};

function renderRecommendation(rec) {
  const box = $("#result");
  box.hidden = false;
  const pick = rec.ranked[0];
  const conf = rec.confidence || {};

  const failed = rec.failed && rec.failed.length
    ? `<div class="callout warn">Could not score: ${rec.failed
        .map(f => `<strong>${esc(f.ticker)}</strong> &mdash; ${esc(f.error)}`).join("; ")}</div>` : "";

  box.innerHTML = `
    <div class="panel">
      <div class="verdict-bar">
        <div>
          <div class="slot-label">Best pick</div>
          <div class="pick">${esc(rec.pick)}</div>
        </div>
        <div>
          <div class="slot-label">Score</div>
          <div class="pick">${num(rec.pick_score, 1)}</div>
        </div>
        <div class="why">
          <span class="pill ${esc(conf.level)}">${esc((conf.level || "").toUpperCase())}</span>
          ${esc(conf.note || "")}
          <div style="margin-top:5px">Scored on the close of
            <strong>${esc(rec.as_of)}</strong>.
            ${rec.recommendation_id
              ? `Saved as recommendation <strong>#${rec.recommendation_id}</strong>.`
              : `<em>Not saved.</em>`}</div>
        </div>
        ${rec.recommendation_id ? `<button class="primary" id="buy-pick">Log a buy of ${esc(rec.pick)}</button>` : ""}
      </div>
      ${failed}
      <div class="callout">
        <strong>What the score is.</strong> A weighted blend of the five components
        below, mapped onto 0&ndash;100 where 50 is neutral. It ranks these candidates
        against each other &mdash; it is <em>not</em> a probability that the trade wins.
        The honest probability is on the <strong>Track record</strong> tab, and only
        becomes meaningful once enough of these calls have been closed out.
      </div>
    </div>
    ${comparisonChart(rec.ranked)}
    <div class="panel">
      <div class="rank-note">
        <strong>${rec.ranked.length} candidates, ranked best first.</strong>
        Reading order is the ranking &mdash; left to right, then down.
        ${rec.ranked.length > 1 ? `Scores run from
          <strong>${num(rec.ranked[0].score, 1)}</strong> down to
          <strong>${num(rec.ranked[rec.ranked.length - 1].score, 1)}</strong>.` : ""}
      </div>
      <div class="cards">
        ${rec.ranked.map((r, i) => card(r, i === 0)).join("")}
      </div>
    </div>`;

  if ($("#buy-pick")) $("#buy-pick").onclick = () => openTrade(rec.recommendation_id, rec.pick, pick.price);
  $$("#result [data-buy]").forEach(b => b.onclick = () =>
    openTrade(rec.recommendation_id, b.dataset.buy, +b.dataset.price));
  wireChartHover(rec);
}

/* ------------------------------------------------- two-week comparison chart */

/* How many closes the score chart covers. Read from the server at startup so
   the copy on screen can never disagree with the data the server sent -- the
   value lives in app/config.py and nowhere else. */
let COMPARE_SESSIONS = 10;

/** Colour by ENTITY, never by rank.
 *
 *  If hue followed the ranking, re-running the analysis would repaint every
 *  line whenever the order changed, and a reader who learned "MSFT is blue"
 *  would be misled. Slot order in the hotlist is stable across runs, so that is
 *  what drives the assignment; anything scored outside the hotlist is appended
 *  alphabetically so it too is stable. */
function seriesColours(tickers) {
  const ordered = [];
  for (let slot = 1; slot <= state.slots; slot++) {
    const h = state.hotlist.find(x => x.slot === slot);
    if (h && tickers.includes(h.ticker)) ordered.push(h.ticker);
  }
  tickers.filter(t => !ordered.includes(t)).sort().forEach(t => ordered.push(t));
  const map = {};
  // 8 validated slots and hotlist_size is 8, so the modulo never actually
  // wraps -- it is a guard, not a cycle. Cycling hues would put two entities
  // in the same colour.
  ordered.forEach((t, i) => { map[t] = `var(--series-${(i % 8) + 1})`; });
  return map;
}

/** Each candidate's SCORE over the window, plus its price move for context.
 *
 *  The chart plots the score, not the price change. A price-change chart puts
 *  whatever has already run hardest at the top and reads as a ranking -- which
 *  directly contradicts the model, since the scoring deliberately penalises
 *  stocks that have gone vertical (the RSI-above-75 penalty and the extension
 *  rule). Charting the score instead answers the question the app is for: is
 *  this setup building or decaying? The price move stays, as a table column,
 *  where it informs without claiming to be the ranking. */
function relativeSeries(ranked, sessions = COMPARE_SESSIONS) {
  const out = [];
  for (const r of ranked) {
    if (!r.ok || !Array.isArray(r.score_history) || r.score_history.length < 2) continue;
    const hist = r.score_history;

    // Price move over the same window, for the table only.
    let move = null;
    if (r.chart && r.chart.close) {
      const closes = r.chart.close.filter(c => c !== null && c !== undefined);
      const win = closes.slice(-(sessions + 1));
      if (win.length >= 2 && win[0]) move = (100 * (win[win.length - 1] - win[0])) / win[0];
    }

    out.push({
      ticker: r.ticker, score: r.score, rank: r.rank, move,
      dates: hist.map(h => h.date),
      pct: hist.map(h => h.score),          // the plotted value: the score
      delta: hist[hist.length - 1].score - hist[0].score,
    });
  }
  return out;
}

/** Your own trades, drawn onto the score line.
 *
 *  A shaded band spans the holding period and a marker sits at every buy and
 *  sell -- placed at the SCORE on that date, not at a price. That is the point:
 *  it shows what the model thought at the moment you acted, which is the one
 *  comparison the cards cannot make. Buying at 73 and watching it climb is a
 *  different lesson from buying at 73 on the way down.
 */
function tradeMarks(series, colours, x, y, dates) {
  const marks = [];
  let bands = "", glyphs = "";
  const n = dates.length;
  // Last chart index at or before a date; -1 when it precedes the window.
  const idxAt = d => {
    let k = -1;
    for (let i = 0; i < n; i++) if (dates[i] <= d) k = i;
    return k;
  };
  const money = v => (v > 0 ? "+" : "") + num(v);

  for (const s of series) {
    for (const t of (state.trades || []).filter(t => t.ticker === s.ticker)) {
      const e = t.econ || {};
      const closed = t.status === "closed";
      const lastExit = t.exits && t.exits.length
        ? t.exits[t.exits.length - 1].exit_date : null;
      const startIdx = idxAt(t.entry_date);
      const endIdx = closed ? idxAt(lastExit) : n - 1;
      if (endIdx < 0) continue;                       // wholly before the window

      const from = Math.max(0, startIdx);
      const to = Math.max(from, endIdx);
      // A same-day round trip would otherwise be a zero-width rectangle.
      const x0 = x(from), x1 = Math.max(x(to), x(from) + 5);

      // Where the position stands, shown on every marker of this trade.
      let summary;
      if (closed) {
        summary = `<div class="tip-sum">Closed &middot; realised
          <span class="${e.realised_pnl > 0 ? "up" : "down"} mono">${money(e.realised_pnl)}</span>
          <span class="mono">(${e.realised_return_pct > 0 ? "+" : ""}${
            num(e.realised_return_pct, 2)}%)</span></div>
          ${t.outcome && t.outcome.verdict
            ? `<div class="tip-note">${esc(t.outcome.verdict)}</div>` : ""}`;
      } else {
        // Marked to the live price already on the card -- no extra request.
        const live = (state.lastRec.ranked.find(r => r.ticker === t.ticker) || {}).live;
        const mark = live ? live.price : null;
        const un = mark !== null && e.open_avg_cost
          ? e.qty_open * (mark - e.open_avg_cost) : null;
        const unPct = mark !== null && e.open_avg_cost
          ? (100 * (mark - e.open_avg_cost)) / e.open_avg_cost : null;
        summary = `<div class="tip-sum">Open &middot; ${shareText(e.qty_open)} sh at
          <span class="mono">${num(e.open_avg_cost, 2)}</span>${
          un === null ? "" : ` &middot; <span class="${un > 0 ? "up" : "down"} mono">${
            money(un)}</span> <span class="mono">(${unPct > 0 ? "+" : ""}${
            num(unPct, 2)}%)</span>`}</div>
          ${mark !== null ? `<div class="tip-note">marked to ${esc(live.label)} ${
            num(mark, 2)}</div>` : ""}`;
      }

      bands += `<rect x="${x0.toFixed(1)}" y="${y(100).toFixed(1)}"
        width="${(x1 - x0).toFixed(1)}" height="${(y(0) - y(100)).toFixed(1)}"
        fill="${colours[s.ticker]}" opacity="0.10"/>
        <line x1="${x0.toFixed(1)}" y1="${y(100).toFixed(1)}"
              x2="${x0.toFixed(1)}" y2="${y(0).toFixed(1)}"
              stroke="${colours[s.ticker]}" stroke-width="1" opacity="0.45"
              vector-effect="non-scaling-stroke"/>`;

      const event = (dateStr, qty, price, kind) => {
        const i = idxAt(dateStr);
        if (i < 0) return;
        const cx = x(i), cy = y(s.pct[i]);
        const up = kind === "bought";
        const tri = up
          ? `${cx},${cy - 6.5} ${cx - 5},${cy + 3} ${cx + 5},${cy + 3}`
          : `${cx},${cy + 6.5} ${cx - 5},${cy - 3} ${cx + 5},${cy - 3}`;
        glyphs += `<polygon points="${tri}" fill="${colours[s.ticker]}"
          stroke="var(--surface)" stroke-width="2" class="trademark"/>
          <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="13"
                  fill="transparent" class="tradehit" data-mark="${marks.length}"/>`;
        marks.push({
          html: `<div class="tip-date">${esc(dateStr)} &middot; ${kind}</div>
            <div class="tip-row"><span class="swatch"
              style="background:${colours[s.ticker]}"></span>
              <span class="mono">${esc(s.ticker)}</span>
              <span class="num mono">${shareText(qty)} @ ${num(price, 2)}</span></div>
            <div class="tip-note">score that day <strong>${num(s.pct[i], 1)}</strong></div>
            ${summary}`,
        });
      };

      (t.lots || []).forEach(l => event(l.lot_date, l.qty, l.price, "bought"));
      (t.exits || []).forEach(xx => event(xx.exit_date, xx.qty, xx.price, "sold"));
    }
  }
  return { bands, glyphs, marks, any: marks.length > 0 };
}

/** Direct labels at the line ends, pushed apart so none overprints another.
 *
 *  Three candidates finishing within half a percent of each other rendered
 *  their labels on top of one another and became unreadable. The dot stays at
 *  the true value; only the text is nudged, with a leader line whenever the
 *  nudge is big enough to notice. */
function endLabels(byMove, y, colours, x, top, bottom) {
  const GAP = 11.5;                      // viewBox units, ~1.4 line heights
  const items = byMove.map(s => {
    const v = s.pct[s.pct.length - 1];
    return { s, v, at: y(v), to: y(v) };
  }).sort((a, b) => a.at - b.at);

  for (let i = 1; i < items.length; i++) {
    if (items[i].to - items[i - 1].to < GAP) items[i].to = items[i - 1].to + GAP;
  }
  // If the stack overflowed the plot, walk back up from the bottom.
  const overflow = items.length && items[items.length - 1].to - bottom;
  if (overflow > 0) {
    items[items.length - 1].to = bottom;
    for (let i = items.length - 2; i >= 0; i--) {
      if (items[i + 1].to - items[i].to < GAP) items[i].to = items[i + 1].to - GAP;
    }
    if (items[0].to < top) items[0].to = top;
  }

  return items.map(({ s, v, at, to }) => {
    const moved = Math.abs(to - at) > 2;
    const leader = moved
      ? `<line x1="${(x - 5).toFixed(1)}" y1="${at.toFixed(1)}"
               x2="${(x - 1).toFixed(1)}" y2="${to.toFixed(1)}"
               stroke="${colours[s.ticker]}" stroke-width="1" opacity=".55"
               vector-effect="non-scaling-stroke"/>` : "";
    return `${leader}<text x="${x}" y="${(to + 3.5).toFixed(1)}" class="endlabel"
      fill="${colours[s.ticker]}">${esc(s.ticker)} ${v.toFixed(1)}</text>`;
  }).join("");
}

function comparisonChart(ranked) {
  const series = relativeSeries(ranked);
  if (series.length < 2) return "";
  const colours = seriesColours(series.map(s => s.ticker));
  const dates = series[0].dates;
  const n = dates.length;

  // A fixed 10-point grid. The axis is always a 0..100 score, so there is
  // nothing to infer: tens read naturally, 50 is always a tick, and the
  // clamped range never yields more than a handful of lines. A "nice step"
  // heuristic was worse here -- a span of 50.2 tipped its magnitude bucket and
  // silently produced 20-point steps that skipped straight over neutral.
  const step = 10;
  const all = series.flatMap(s => s.pct);
  const lo = Math.max(0, Math.floor((Math.min(50, ...all) - 3) / step) * step);
  const hi = Math.min(100, Math.ceil((Math.max(50, ...all) + 3) / step) * step);

  // The container includes the x-axis band, so labels are never clipped.
  const W = 760, H = 250, L = 46, R = 96, T = 14, B = 30;
  const x = i => L + (i * (W - L - R)) / (n - 1);
  const y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo || 1));

  const neutral = y(50);
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(v);

  const grid = ticks.map(v => `
    <line x1="${L}" y1="${y(v).toFixed(1)}" x2="${W - R}" y2="${y(v).toFixed(1)}"
          stroke="var(--grid)"
          stroke-width="1" vector-effect="non-scaling-stroke"/>
    <text x="${L - 7}" y="${(y(v) + 3.5).toFixed(1)}" text-anchor="end"
          class="ax">${v.toFixed(0)}</text>`).join("");

  // Aim for about six date labels whatever the window length, and suppress a
  // periodic label that would land next to the final one -- at a fixed stride
  // this printed "09-02" and "09-03" on top of each other.
  const stride = Math.max(1, Math.ceil(n / 6));
  const xlabels = dates.map((d, i) =>
    (i === n - 1 || (i % stride === 0 && i <= n - 1 - Math.ceil(stride / 2)))
      ? `<text x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="${
          i === n - 1 ? "end" : "middle"}" class="ax">${esc(d.slice(5))}</text>`
      : "").join("");

  const overlay = tradeMarks(series, colours, x, y, dates);
  state.chartMarks = overlay.marks;

  const lines = series.map(s => {
    const d = s.pct.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
    const last = s.pct[s.pct.length - 1];
    return `<path d="${d}" fill="none" stroke="${colours[s.ticker]}" stroke-width="2"
                  stroke-linejoin="round" stroke-linecap="round"
                  vector-effect="non-scaling-stroke"/>
            <circle cx="${x(n - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="4"
                    fill="${colours[s.ticker]}" stroke="var(--surface)" stroke-width="2"/>`;
  }).join("");

  // Ordered by the score, so the chart's own ranking matches the cards below.
  const byMove = [...series].sort((a, b) =>
    b.pct[b.pct.length - 1] - a.pct[a.pct.length - 1]);

  return `<div class="panel compare">
    <div class="panel-head">
      <h2>How the scores got here</h2>
      <p class="sub">Each candidate's score recomputed at every one of the last
        ${COMPARE_SESSIONS} closes, so the line shows whether a setup is
        <strong>building or decaying</strong> &mdash; not just where it landed
        today. <strong>50 is neutral.</strong> The top line here is the top card
        below; they cannot disagree, because they are the same number.${
          overlay.any ? ` <strong>Your own trades are shaded on the chart</strong>
          &mdash; hover a triangle for what you did and where the position
          stands.` : ""}</p>
    </div>
    <div class="chartwrap" tabindex="0" role="img"
         aria-label="Score of ${series.length} candidates at each of the last
           ${COMPARE_SESSIONS} closes, 0 to 100 with 50 neutral. Values are
           listed in the table below."
         data-dates="${esc(dates.join(","))}">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        ${grid}${xlabels}
        ${overlay.bands}
        <line x1="${L}" y1="${neutral.toFixed(1)}" x2="${W - R}" y2="${neutral.toFixed(1)}"
              stroke="var(--axis)" stroke-width="1" vector-effect="non-scaling-stroke"/>
        <text x="${W - R - 3}" y="${(neutral - 5).toFixed(1)}" text-anchor="end"
              class="ax">neutral</text>
        ${lines}
        ${overlay.glyphs}
        <g class="crosshair" hidden>
          <line y1="${T}" y2="${H - B}" stroke="var(--ink-3)" stroke-width="1"
                vector-effect="non-scaling-stroke"/>
        </g>
        <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"
              fill="transparent" class="hit"/>
        ${endLabels(byMove, y, colours, W - R + 8, T, H - B)}
      </svg>
      <div class="chart-tip" hidden></div>
    </div>

    <div class="table-wrap">
      <table class="compare-table">
        <thead><tr><th>Candidate</th><th class="num">Score</th>
          <th class="num">Change over ${COMPARE_SESSIONS}</th>
          <th class="num">Price move</th><th class="num">Rank</th></tr></thead>
        <tbody>${byMove.map(s => `<tr>
          <td><span class="swatch" style="background:${colours[s.ticker]}"></span>
              <span class="mono">${esc(s.ticker)}</span></td>
          <td class="num"><strong>${num(s.pct[s.pct.length - 1], 1)}</strong></td>
          <td class="num ${s.delta > 0 ? "up" : s.delta < 0 ? "down" : "flat"} mono">${
            s.delta > 0 ? "+" : ""}${num(s.delta, 1)}</td>
          <td class="num">${s.move === null ? "&mdash;" : pct(s.move, 2)}</td>
          <td class="num">${s.rank}</td></tr>`).join("")}
        </tbody>
      </table>
    </div>
  </div>`;
}

/** Crosshair + tooltip. A tooltip never gates a value -- everything it shows is
 *  also in the table above, and arrow keys drive it for keyboard users. */
function wireChartHover(rec) {
  const wrap = $("#result .chartwrap");
  if (!wrap) return;
  const svg = $("svg", wrap);
  const tip = $(".chart-tip", wrap);
  const cross = $(".crosshair", wrap);
  const line = $("line", cross);
  const series = relativeSeries(rec.ranked);
  const colours = seriesColours(series.map(s => s.ticker));
  const dates = wrap.dataset.dates.split(",");
  const n = dates.length;
  const L = 46, R = 96, W = 760;
  let at = -1;

  const show = i => {
    if (i < 0 || i >= n) return;
    at = i;
    const px = L + (i * (W - L - R)) / (n - 1);
    line.setAttribute("x1", px);
    line.setAttribute("x2", px);
    cross.hidden = false;
    const rows = series
      .map(s => ({ t: s.ticker, v: s.pct[i] }))
      .sort((a, b) => b.v - a.v);
    tip.innerHTML = `<div class="tip-date">${esc(dates[i])}</div>` +
      rows.map(r => `<div class="tip-row">
        <span class="swatch" style="background:${colours[r.t]}"></span>
        <span class="mono">${esc(r.t)}</span>
        <span class="num mono ${r.v > 50 ? "up" : r.v < 50 ? "down" : "flat"}">${
          num(r.v, 1)}</span></div>`).join("");
    tip.hidden = false;
    const frac = px / W;
    tip.style.left = `${Math.min(78, Math.max(2, frac * 100))}%`;
  };
  const hide = () => { cross.hidden = true; tip.hidden = true; at = -1; };

  // Trade markers get their own tooltip and win over the crosshair: a triangle
  // is a specific thing you did, and reading the whole field at that date is
  // not what someone hovering it wants. The hit circles are 26px across, so
  // there is no pinpoint target.
  let onMark = false;
  $$(".tradehit", svg).forEach(hit => {
    hit.addEventListener("pointerenter", () => {
      const m = (state.chartMarks || [])[+hit.dataset.mark];
      if (!m) return;
      onMark = true;
      cross.hidden = true;
      tip.innerHTML = m.html;
      tip.hidden = false;
      const frac = (+hit.getAttribute("cx")) / W;
      tip.style.left = `${Math.min(64, Math.max(2, frac * 100))}%`;
    });
    hit.addEventListener("pointerleave", () => { onMark = false; hide(); });
  });

  // The hit area is the whole plot, so there is no pinpoint target to land on.
  svg.addEventListener("pointermove", ev => {
    if (onMark) return;
    const box = svg.getBoundingClientRect();
    const vx = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.round(((vx - L) / (W - L - R)) * (n - 1));
    if (i >= 0 && i < n) show(i); else hide();
  });
  svg.addEventListener("pointerleave", hide);
  wrap.addEventListener("keydown", ev => {
    if (ev.key === "ArrowRight") { show(at < 0 ? 0 : Math.min(n - 1, at + 1)); ev.preventDefault(); }
    if (ev.key === "ArrowLeft") { show(at < 0 ? n - 1 : Math.max(0, at - 1)); ev.preventDefault(); }
    if (ev.key === "Escape") hide();
  });
  wrap.addEventListener("blur", hide);
}

const COMPONENT_LABEL = {
  trend: "Trend", momentum: "Momentum", structure: "Structure",
  volume: "Volume", risk: "Risk",
};

/** The two prices on a candidate card, and the gap between them.
 *
 *  The big number above stays the SCORED close, because the whole card -- the
 *  score, the components, the entry, stop and target -- is derived from it.
 *  The live price sits underneath as context: it says how far the stock has
 *  already moved since the analysis those levels came from, which is exactly
 *  what you need to know before acting on them. */
function liveLine(r) {
  const q = r.live;
  const scored = `<span class="flat">scored on close ${esc(r.as_of)}</span>
                  <strong class="mono">${num(r.price)}</strong>`;
  if (!q) return `${scored} <span class="flat">&middot; no live quote</span>`;
  const tag = `<span class="mark-note" title="Latest traded price. The score and
    the trade plan above come from the daily close, not from this.">${esc(q.label)}
    ${num(q.price)}</span>`;
  // Only show a move when the market is actually open. Once it has closed, the
  // quote and the scored close are the same session, and the small gap between
  // the official close and the last printed minute is not a price move --
  // rendering it as one invented a red arrow on a day nothing had happened.
  if (!q.is_live) return `${scored} ${tag}`;
  const drift = q.change_pct;
  const arrow = drift > 0 ? "&#9650;" : drift < 0 ? "&#9660;" : "";
  return `${scored} ${tag}
    <span class="${drift > 0 ? "up" : drift < 0 ? "down" : "flat"} mono">${arrow}
      ${drift > 0 ? "+" : ""}${num(drift, 2)}%</span>`;
}

/** Refresh just the quotes on the cards, without re-scoring or re-saving. */
async function refreshCardQuotes() {
  const boxes = $$("#result [data-live]");
  if (!boxes.length || !state.lastRec) return;
  const tickers = boxes.map(b => b.dataset.live);
  try {
    const { quotes } = await api(`/api/quotes?tickers=${encodeURIComponent(tickers.join(","))}`);
    for (const r of state.lastRec.ranked) {
      const q = quotes[r.ticker];
      r.live = q ? { ...q, change_pct: q.price && r.price
        ? Math.round(((q.price - r.price) / r.price) * 100000) / 1000 : null } : null;
      const box = $(`#result [data-live="${CSS.escape(r.ticker)}"]`);
      if (box) box.innerHTML = liveLine(r);
    }
  } catch { /* stale quotes are not worth an error message */ }
}

function card(r, winner) {
  if (!r.ok) {
    return `<div class="card"><div class="card-head"><span class="tick">${esc(r.ticker)}</span></div>
            <div class="card-body err">${esc(r.error)}</div></div>`;
  }
  const hue = r.score >= 60 ? "var(--up)" : r.score >= 45 ? "var(--warn)" : "var(--down)";
  const comps = Object.keys(COMPONENT_LABEL).map(k => {
    const v = r.components[k] ?? 0;
    const w = Math.abs(v) * 50;
    const left = v >= 0 ? 50 : 50 - w;
    const col = v >= 0 ? "var(--up)" : "var(--down)";
    return `<div class="comp">
      <span class="cname">${COMPONENT_LABEL[k]}</span>
      <span class="bipolar"><i style="left:${left}%;width:${w}%;background:${col}"></i></span>
      <span class="cval">${v >= 0 ? "+" : ""}${num(v, 2)}</span>
    </div>`;
  }).join("");

  const p = r.plan;
  return `<div class="card ${winner ? "winner" : ""}">
    <div class="card-head">
      <span class="tick">${esc(r.ticker)}</span>
      <span class="rank">${winner ? "Pick" : "#" + r.rank}</span>
      <span class="price">${num(r.price)}</span>
    </div>
    <div class="livebar" data-live="${esc(r.ticker)}">${liveLine(r)}</div>
    <div class="card-body">
      <div class="score-row">
        <span class="score-num" style="color:${hue}">${num(r.score, 1)}</span>
        <span class="score-max">/ 100</span>
        <span class="score-verdict" style="color:${hue}">${esc(r.verdict)}</span>
      </div>
      <div class="meter"><i style="width:${Math.max(0, Math.min(100, r.score))}%;background:${hue}"></i></div>
      <div class="meter-scale"><span>0</span><span>50 neutral</span><span>100</span></div>

      ${sparkline(r.chart)}

      <div class="components">${comps}</div>

      <div class="plan">
        <div><dt>Entry</dt><dd>${num(p.entry)}</dd></div>
        <div><dt>Stop</dt><dd class="down">${num(p.stop)}</dd></div>
        <div><dt>Target (${p.reward_risk}R)</dt><dd class="up">${num(p.target)}</dd></div>
        <div><dt>Risk</dt><dd>${num(p.risk_pct, 1)}%</dd></div>
      </div>
      <div class="sig-note" style="margin-top:6px">Stop from the ${esc(p.stop_basis)}.</div>

      <details class="signals">
        <summary>Why &mdash; all ${r.signals.length} signals</summary>
        <div class="table-wrap"><table>
          <thead><tr><th>Signal</th><th class="num">Reading</th><th class="num">Score</th></tr></thead>
          <tbody>${r.signals.map(s => `<tr>
            <td><div>${esc(s.label)}</div>
                <div class="comp-tag">${esc(s.component)}</div>
                <div class="sig-note">${esc(s.note)}</div></td>
            <td class="num">${esc(s.display)}</td>
            <td class="num ${s.score > 0 ? "up" : s.score < 0 ? "down" : "flat"}">${
              s.score > 0 ? "+" : ""}${num(s.score, 2)}</td></tr>`).join("")}
          </tbody></table></div>
      </details>

      <div class="actions">
        <button class="tiny" data-buy="${esc(r.ticker)}" data-price="${r.price}">Log a buy</button>
      </div>
    </div></div>`;
}

/* ------------------------------------------------------------- sparkline */

function sparkline(c) {
  if (!c || !c.close || c.close.length < 2) return "";
  const W = 340, H = 96, P = 4;
  const series = [c.close, c.ema_fast, c.ema_mid];
  const flat = series.flat().filter(v => v !== null);
  const lo = Math.min(...flat), hi = Math.max(...flat);
  const span = (hi - lo) || 1;
  const n = c.close.length;
  const x = i => P + (i * (W - 2 * P)) / (n - 1);
  const y = v => P + (H - 2 * P) * (1 - (v - lo) / span);

  const path = arr => {
    let d = "", pen = false;
    arr.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1) + " ";
      pen = true;
    });
    return d.trim();
  };

  const first = c.close.find(v => v !== null);
  const last = c.close[c.close.length - 1];
  const dir = last >= first ? "var(--up)" : "var(--down)";
  const area = `${path(c.close)} L${x(n - 1).toFixed(1)} ${H - P} L${x(0).toFixed(1)} ${H - P} Z`;
  const uid = "g" + Math.random().toString(36).slice(2, 8);

  return `<div class="chart">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
         aria-label="${c.dates.length} sessions of closing price, ending ${esc(c.dates[c.dates.length-1])}">
      <defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${dir}" stop-opacity=".22"/>
        <stop offset="100%" stop-color="${dir}" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${area}" fill="url(#${uid})"/>
      <path d="${path(c.ema_mid)}" fill="none" stroke="var(--ink-3)" stroke-width="1"
            stroke-dasharray="3 2" vector-effect="non-scaling-stroke"/>
      <path d="${path(c.ema_fast)}" fill="none" stroke="var(--accent)" stroke-width="1"
            vector-effect="non-scaling-stroke"/>
      <path d="${path(c.close)}" fill="none" stroke="${dir}" stroke-width="1.6"
            vector-effect="non-scaling-stroke"/>
    </svg>
    <div class="legend">
      <span><i style="background:${dir}"></i>Close</span>
      <span><i style="background:var(--accent)"></i>EMA20</span>
      <span><i style="background:var(--ink-3)"></i>EMA50</span>
      <span style="margin-left:auto">${esc(c.dates[0])} &rarr; ${esc(c.dates[c.dates.length - 1])}</span>
    </div></div>`;
}

/* ---------------------------------------------------------------- trades */

/* ------------------------------------------------------------- buy dialog */

/** A position is often built in several packages at different prices, so the
 *  form takes a list of them rather than one quantity and one price. `mode` is
 *  either a new position or an addition to an open one. */
const buy = { mode: "new", ticker: "", recId: null, tradeId: null, price: null };

function openBuyDialog(opts) {
  Object.assign(buy, opts);
  $("#buy-title").textContent = buy.mode === "add"
    ? `Buy more ${buy.ticker}` : `Log a buy of ${buy.ticker}`;
  $("#buy-sub").innerHTML = buy.mode === "add"
    ? `Added to the open position. The cost basis becomes the weighted average
       of every package, and the grading window still starts at the first buy.`
    : (buy.recId
        ? `Linked to recommendation <strong>#${buy.recId}</strong>, so closing it
           will grade the pick against the other candidates.`
        : `Not linked to a recommendation - P&amp;L only, no grading.`);
  $("#buy-note").value = "";
  $("#lot-rows").innerHTML = "";
  addLotRow();
  $("#buy-dialog").showModal();
  setTimeout(() => $("#lot-rows input[data-k=qty]").focus(), 30);
}

function addLotRow(qty = "", price = null) {
  const row = document.createElement("div");
  row.className = "lot-row";
  row.innerHTML = `
    <input type="date" data-k="date" value="${today()}">
    <input type="number" data-k="qty" step="any" min="0" placeholder="0" value="${qty}">
    <input type="number" data-k="price" step="any" min="0" placeholder="0.00"
           value="${price ?? (buy.price ?? "")}">
    <input type="number" data-k="fee" step="any" min="0" placeholder="0" value="">
    <button type="button" class="tiny danger" data-drop title="Remove">&times;</button>`;
  $("[data-drop]", row).onclick = () => {
    if ($$("#lot-rows .lot-row").length <= 1) return;   // never leave zero rows
    row.remove();
    paintBuyTotal();
  };
  $$("input", row).forEach(i => i.addEventListener("input", paintBuyTotal));
  $("#lot-rows").appendChild(row);
  paintBuyTotal();
}

$("#add-lot").onclick = () => {
  // A second package is usually the same day at a slightly different price, so
  // carry the last row's price rather than starting blank.
  const rows = readLotRows();
  addLotRow("", rows.length ? rows[rows.length - 1].price : null);
};

function readLotRows() {
  return $$("#lot-rows .lot-row").map(r => ({
    lot_date: $("[data-k=date]", r).value || today(),
    qty: Number($("[data-k=qty]", r).value.replace(",", ".")) || 0,
    price: Number($("[data-k=price]", r).value.replace(",", ".")) || 0,
    fee: Number($("[data-k=fee]", r).value.replace(",", ".")) || 0,
  }));
}

function paintBuyTotal() {
  const rows = readLotRows().filter(r => r.qty > 0 && r.price > 0);
  const el = $("#buy-total");
  if (!rows.length) {
    el.innerHTML = `<span class="flat">Enter shares and a price.</span>`;
    return;
  }
  const shares = rows.reduce((s, r) => s + r.qty, 0);
  const cost = rows.reduce((s, r) => s + r.qty * r.price + r.fee, 0);
  el.innerHTML = `<strong>${num(shares, shares % 1 ? 4 : 0)} shares</strong>
    &middot; average <strong>${num(cost / shares, 4)}</strong>
    &middot; total cost <strong>${num(cost)}</strong>
    ${rows.some(r => r.fee) ? " (fees included in the average)" : ""}`;
}

$("#buy-cancel").onclick = () => $("#buy-dialog").close();

$("#buy-ok").onclick = async () => {
  const rows = readLotRows();
  const bad = rows.find(r => !(r.qty > 0) || !(r.price > 0));
  if (bad) { toast("Every package needs a share count and a price.", true); return; }

  const btn = $("#buy-ok");
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Saving';
  try {
    if (buy.mode === "add") {
      // One call per package: the server recomputes the average after each.
      for (const r of rows) await api(`/api/trades/${buy.tradeId}/lots`,
                                      { method: "POST", body: r });
      toast(`Added ${rows.length} package(s) to ${buy.ticker}.`);
    } else {
      await api("/api/trades", {
        method: "POST",
        body: { ticker: buy.ticker, recommendation_id: buy.recId || null,
                lots: rows, note: $("#buy-note").value.trim() || null },
      });
      toast(`Logged ${buy.ticker}: ${rows.length} package(s).`);
    }
    $("#buy-dialog").close();
    $$("#tabs button").find(b => b.dataset.tab === "trades").click();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Log buy";
  }
};

function openTrade(recId, ticker, price) {
  openBuyDialog({ mode: "new", ticker, recId: recId || null, tradeId: null, price });
}

async function loadTrades() {
  const d = await api("/api/trades");
  const open = d.trades.filter(t => t.status === "open");
  const closed = d.trades.filter(t => t.status === "closed");

  $("#open-trades").innerHTML = open.length
    ? open.map(openTradeCard).join("")
    : `<div class="empty">No open positions. Run an analysis and log a buy.</div>`;
  $("#closed-trades").innerHTML = closed.length
    ? closed.map(closedTradeCard).join("")
    : `<div class="empty">Nothing closed yet.</div>`;

  $$("[data-sell]").forEach(b => b.onclick = () => doSell(+b.dataset.sell, false));
  $$("[data-sell-all]").forEach(b => b.onclick = () => doSell(+b.dataset.sellAll, true));
  $$("[data-del-exit]").forEach(b => b.onclick = async () => {
    if (!confirm("Undo this sale? If it was the one that closed the position, "
                 + "the position reopens and its grade is removed.")) return;
    try {
      await api(`/api/exits/${b.dataset.delExit}`, { method: "DELETE" });
      loadTrades();
    } catch (e) { toast(e.message, true); }
  });
  $$("[data-del-trade]").forEach(b => b.onclick = async () => {
    if (!confirm("Delete this trade from the log?")) return;
    await api(`/api/trades/${b.dataset.delTrade}`, { method: "DELETE" });
    loadTrades();
  });
  $$("[data-buy-more]").forEach(b => b.onclick = () => openBuyDialog({
    mode: "add", ticker: b.dataset.ticker, tradeId: +b.dataset.buyMore,
    recId: null, price: null,
  }));
  $$("[data-del-lot]").forEach(b => b.onclick = async () => {
    if (!confirm("Remove this package? The average cost will be recalculated.")) return;
    try {
      await api(`/api/lots/${b.dataset.delLot}`, { method: "DELETE" });
      loadTrades();
    } catch (e) { toast(e.message, true); }
  });
  open.forEach(t => markToMarket(t.id));
}

const shareText = q => num(q, q % 1 ? 4 : 0);

/** The packages a position was built from. Shown whenever there is more than
 *  one, because a single weighted average hides what was actually paid. */
function lotsTable(t, editable) {
  const lots = t.lots || [];
  if (lots.length < 2 && !editable) return "";
  const cost = lots.reduce((s, l) => s + l.qty * l.price + (l.fee || 0), 0);
  return `<div class="table-wrap" style="margin-top:10px"><table>
    <thead><tr><th>Bought</th><th class="num">Shares</th><th class="num">Price</th>
      <th class="num">Fee</th><th class="num">Cost</th>${editable ? "<th></th>" : ""}</tr></thead>
    <tbody>${lots.map(l => `<tr>
      <td class="mono">${esc(l.lot_date)}</td>
      <td class="num">${shareText(l.qty)}</td>
      <td class="num">${num(l.price, 4)}</td>
      <td class="num">${l.fee ? num(l.fee) : "&mdash;"}</td>
      <td class="num">${num(l.qty * l.price + (l.fee || 0))}</td>
      ${editable ? `<td class="num">${lots.length > 1
        ? `<button class="tiny danger" data-del-lot="${l.id}" title="Remove this package">&times;</button>`
        : ""}</td>` : ""}
    </tr>`).join("")}</tbody>
    <tfoot><tr>
      <td><strong>${lots.length} package${lots.length === 1 ? "" : "s"}</strong></td>
      <td class="num"><strong>${shareText(t.qty)}</strong></td>
      <td class="num"><strong>${num(t.entry_price, 4)}</strong></td>
      <td class="num"></td>
      <td class="num"><strong>${num(cost)}</strong></td>
      ${editable ? "<td></td>" : ""}
    </tr></tfoot></table></div>`;
}

/** The sales made so far. A position can be scaled out of over several days,
 *  so each one is kept and can be undone. */
function exitsTable(t, editable) {
  const exits = t.exits || [];
  if (!exits.length) return "";
  return `<div class="table-wrap" style="margin-top:10px"><table>
    <thead><tr><th>Sold</th><th class="num">Shares</th><th class="num">Price</th>
      <th class="num">Fee</th><th class="num">Proceeds</th>${editable ? "<th></th>" : ""}</tr></thead>
    <tbody>${exits.map(x => `<tr>
      <td class="mono">${esc(x.exit_date)}</td>
      <td class="num">${shareText(x.qty)}</td>
      <td class="num">${num(x.price, 4)}</td>
      <td class="num">${x.fee ? num(x.fee) : "&mdash;"}</td>
      <td class="num">${num(x.qty * x.price - (x.fee || 0))}</td>
      ${editable ? `<td class="num"><button class="tiny danger" data-del-exit="${x.id}"
        title="Undo this sale">&times;</button></td>` : ""}
    </tr>`).join("")}</tbody>
    <tfoot><tr>
      <td><strong>${exits.length} sale${exits.length === 1 ? "" : "s"}</strong></td>
      <td class="num"><strong>${shareText(t.econ.qty_sold)}</strong></td>
      <td class="num"><strong>${num(t.econ.avg_exit_price, 4)}</strong></td>
      <td class="num"></td>
      <td class="num"><strong>${num(t.econ.proceeds)}</strong></td>
      ${editable ? "<td></td>" : ""}
    </tr></tfoot></table></div>`;
}

function openTradeCard(t) {
  const e = t.econ;
  const part = e.partially_sold;
  return `<div class="trade">
    <div class="trade-head">
      <span class="tick">${esc(t.ticker)}</span>
      <span class="mono">${shareText(e.qty_open)} sh held${
        part ? ` of ${shareText(e.qty_bought)}` : ""}, avg ${num(e.open_avg_cost, 4)}</span>
      <span class="sub" style="margin:0">first bought ${esc(t.entry_date)}${
        (t.lots || []).length > 1 ? ` &middot; ${t.lots.length} packages` : ""}</span>
      ${part ? `<span class="pill slight">part sold</span>` : ""}
      ${t.recommendation_id ? `<span class="pill ${t.followed_pick ? "clear" : "tie"}">
        ${t.followed_pick ? "followed pick" : "overrode pick"} &middot; rec #${t.recommendation_id}</span>` : ""}
      <span class="spacer"></span>
      <span id="mtm-${t.id}" class="flat">&hellip;</span>
    </div>
    <div class="trade-body">
      ${lotsTable(t, true)}
      ${exitsTable(t, true)}
      <div class="actions" style="margin-top:10px">
        <button class="tiny" data-buy-more="${t.id}" data-ticker="${esc(t.ticker)}">
          + Buy more ${esc(t.ticker)}</button>
      </div>
      <div id="live-${t.id}"></div>
      <div class="sell-form">
        <label class="field"><span>Sell date</span>
          <input type="date" id="xd-${t.id}" value="${today()}"></label>
        <label class="field"><span>Shares (max ${shareText(e.qty_open)})</span>
          <input type="number" step="any" min="0" max="${e.qty_open}"
                 id="xq-${t.id}" value="${e.qty_open}"></label>
        <label class="field"><span>Price (blank = that day's close)</span>
          <input type="number" step="any" id="xp-${t.id}" placeholder="auto"></label>
        <label class="field"><span>Fee</span>
          <input type="number" step="any" id="xf-${t.id}" placeholder="0"></label>
        <button class="primary" data-sell="${t.id}">Sell</button>
        <button data-sell-all="${t.id}">Sell all ${shareText(e.qty_open)} &amp; grade</button>
        <button class="danger" data-del-trade="${t.id}">Delete</button>
      </div>
      <p class="sub">Selling everything closes the position and grades the
        recommendation. A partial sale banks the profit on those shares and
        leaves the rest running.</p>
    </div></div>`;
}

async function markToMarket(id) {
  try {
    const r = await api(`/api/trades/${id}/review`);
    const el = $(`#mtm-${id}`);
    if (r.error) { el.innerHTML = `<span class="err">${esc(r.error)}</span>`; return; }
    const e = r.econ || {};
    const money = v => (v > 0 ? "+" : "") + num(v);
    const cls = v => v > 0 ? "up" : v < 0 ? "down" : "flat";
    // Say which price the P&L is marked to. Without this the figure silently
    // meant "yesterday's close" while the market was already trading.
    const q = r.quote;
    const markNote = q
      ? `<span class="mark-note" title="Marked to the latest traded price. Scores and grading still use completed daily closes.">${
          esc(q.label)} ${num(q.price, 2)}</span>`
      : `<span class="mark-note" title="No live quote available - marked to the last daily close.">at close ${
          num(r.last_close, 2)}</span>`;
    // A part-sold position has banked one number and is still risking another.
    // Showing only the blend would hide which is which.
    el.innerHTML = (e.qty_sold
      ? `<span class="${cls(e.realised_pnl)} mono">${money(e.realised_pnl)}</span>
         <span class="flat">banked</span> &middot;
         <span class="${cls(e.unrealised_pnl)} mono">${money(e.unrealised_pnl)}</span>
         <span class="flat">open</span> &middot;
         total ${pct(e.total_return_pct)}
         <span class="flat mono">(${money(e.total_pnl)})</span>`
      : `unrealised ${pct(e.unrealised_return_pct ?? r.traded_return_pct)}` +
        (e.unrealised_pnl != null ? ` <span class="flat mono">(${money(e.unrealised_pnl)})</span>` : "")
      ) + " " + markNote;
    if (r.graded) $(`#live-${id}`).innerHTML = gradeTable(r, true);
  } catch { /* a mark-to-market failure must not break the list */ }
}

function closedTradeCard(t) {
  const o = t.outcome || {};
  // Two independent gradings. The first judges the MODEL (was its pick the best
  // of the field), the second judges the DECISION (was buying this the right
  // call given what the model said). They routinely disagree, so each gets its
  // own callout and its own colour rather than one blended verdict.
  const modelCls = o.graded ? (o.pick_was_best ? "good" : "bad") : "";
  const overrode = o.graded && o.followed_pick === false;
  const cost = o.decision_cost_pct;
  const decisionCls = cost > 0 ? "bad" : cost < 0 ? "good" : "warn";
  return `<div class="trade">
    <div class="trade-head">
      <span class="tick">${esc(t.ticker)}</span>
      <span class="mono">${shareText(t.qty)} sh, ${num(t.entry_price, 4)} &rarr; ${num(t.exit_price, 4)}${
        (t.exits || []).length > 1 ? ` in ${t.exits.length} sales` : ""}</span>
      <span class="sub" style="margin:0">${esc(t.entry_date)} &rarr; ${esc(t.exit_date)}
        (${o.held_days ?? "?"} days)</span>
      ${overrode ? `<span class="pill tie">overrode pick</span>` : ""}
      <span class="spacer"></span>
      <span>${pct(o.traded_return_pct)}${
        o.pnl !== null && o.pnl !== undefined ? ` <span class="flat mono">${num(o.pnl)}</span>` : ""}</span>
      <button class="tiny danger" data-del-trade="${t.id}">Delete</button>
    </div>
    <div class="trade-body">
      ${overrode ? `<div class="callout ${decisionCls}">
        <strong>Your decision:</strong> ${esc(o.decision_verdict)}</div>` : ""}
      <div class="callout ${overrode ? "" : modelCls}">
        ${overrode ? "<strong>The model:</strong> " : ""}${esc(o.verdict || "No verdict recorded.")}</div>
      ${lotsTable(t, false)}
      ${exitsTable(t, false)}
      ${o.graded ? gradeTable(o, false) : ""}
    </div></div>`;
}

/** The comparison that makes a recommendation checkable: every candidate over
 *  the identical window, ranked by what actually happened. */
function gradeTable(o, provisional) {
  const rows = (o.candidates || []).slice()
    .sort((a, b) => (b.return_pct ?? -1e9) - (a.return_pct ?? -1e9));
  const n = rows.filter(r => r.return_pct !== null && r.return_pct !== undefined).length;
  return `<div class="table-wrap" style="margin-top:10px"><table>
    <thead><tr>
      <th>Candidate</th><th class="num">Score then</th><th class="num">Entry</th>
      <th class="num">${provisional ? "Now" : "Exit"}</th><th class="num">Return</th>
      <th>Actual rank</th></tr></thead>
    <tbody>${rows.map(r => {
      const isPick = r.ticker === o.pick;
      const isHeld = r.ticker === o.ticker;
      const rank = r.actual_rank;
      const badge = rank === 1 ? "best" : (rank === n ? "worst" : "mid");
      return `<tr${isPick || isHeld ? ' style="font-weight:600"' : ""}>
        <td>${esc(r.ticker)}${isPick ? ' <span class="pill tie">the pick</span>' : ""}${
            isHeld ? ' <span class="pill slight">you bought</span>' : ""}
            ${r.error ? `<div class="err">${esc(r.error)}</div>` : ""}</td>
        <td class="num">${r.score_at_pick != null ? num(r.score_at_pick, 1) : "&mdash;"}</td>
        <td class="num">${num(r.entry_price)}</td>
        <td class="num">${num(r.exit_price)}</td>
        <td class="num">${pct(r.return_pct)}</td>
        <td>${rank ? `<span class="badge ${badge}">${rank} of ${n}</span>` : "&mdash;"}</td>
      </tr>`;
    }).join("")}</tbody></table></div>
    ${o.opportunity_cost_pct ? `<div class="sub">The model choosing ${esc(o.pick)} over
      ${esc(o.best_ticker)} cost ${num(o.opportunity_cost_pct)} percentage points${
      provisional ? " so far" : ""}.</div>` : ""}`;
}

async function doSell(id, all) {
  const priceRaw = $(`#xp-${id}`).value.trim();
  const feeRaw = $(`#xf-${id}`).value.trim();
  const qtyRaw = $(`#xq-${id}`).value.trim();
  const body = {
    exit_date: $(`#xd-${id}`).value || today(),
    // Omitting the quantity means "everything still held", decided server-side
    // against the live position rather than against what this page last drew.
    qty: all || qtyRaw === "" ? null : Number(qtyRaw.replace(",", ".")),
    price: priceRaw === "" ? null : Number(priceRaw.replace(",", ".")),
    fee: feeRaw === "" ? 0 : Number(feeRaw.replace(",", ".")),
  };
  try {
    const t = await api(`/api/trades/${id}/sell`, { method: "POST", body });
    if (t.status === "closed") {
      toast(t.outcome && t.outcome.verdict ? t.outcome.verdict : "Position closed.");
    } else {
      const e = t.econ;
      toast(`Sold ${shareTextPlain(e.qty_sold)} of ${shareTextPlain(e.qty_bought)}. `
            + `${shareTextPlain(e.qty_open)} still held, `
            + `${e.realised_pnl >= 0 ? "+" : ""}${e.realised_pnl.toFixed(2)} banked.`);
    }
    loadTrades();
  } catch (e) { toast(e.message, true); }
}

const shareTextPlain = q => (q % 1 ? q.toFixed(4) : String(q));

/* --------------------------------------------------------------- history */

async function loadHistory() {
  const d = await api("/api/recommendations");
  const box = $("#history");
  if (!d.recommendations.length) {
    box.innerHTML = `<div class="empty">No recommendations logged yet.</div>`;
    return;
  }
  box.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>#</th><th>When</th><th>Scored on</th><th>Pick</th>
      <th class="num">Score</th><th class="num">Margin</th><th>Candidates</th>
      <th>Since then</th><th></th></tr></thead>
    <tbody>${d.recommendations.map(r => `<tr>
      <td class="mono">${r.id}</td>
      <td>${esc(r.created_at.replace("T", " "))}</td>
      <td class="mono">${esc(r.as_of)}</td>
      <td class="mono"><strong>${esc(r.pick)}</strong></td>
      <td class="num">${num(r.pick_score, 1)}</td>
      <td class="num">${r.margin != null ? num(r.margin, 1) : "&mdash;"}</td>
      <td class="mono">${r.tickers.map(esc).join(" ")}</td>
      <td id="since-${r.id}" class="flat">&hellip;</td>
      <td><button class="tiny danger" data-del-rec="${r.id}">Delete</button></td>
    </tr>`).join("")}</tbody></table></div>`;

  $$("[data-del-rec]").forEach(b => b.onclick = async () => {
    if (!confirm("Delete this recommendation? Any trade linked to it keeps its own record.")) return;
    await api(`/api/recommendations/${b.dataset.delRec}`, { method: "DELETE" });
    loadHistory();
  });

  for (const r of d.recommendations.slice(0, 25)) reviewSince(r.id);
}

async function reviewSince(id) {
  try {
    const r = await api(`/api/recommendations/${id}/review`);
    const el = $(`#since-${id}`);
    if (!el) return;
    if (!r.sessions_elapsed) {
      // Scored on the most recent close, so every return is 0.0 by definition.
      el.innerHTML = `<span class="flat">no session yet</span>`;
      return;
    }
    const rows = r.candidates.filter(c => c.return_pct != null)
      .sort((a, b) => b.return_pct - a.return_pct);
    if (!rows.length) { el.innerHTML = "&mdash;"; return; }
    el.innerHTML = rows.map(c =>
      `<span class="mono" style="margin-right:9px${c.ticker === r.pick ? ";font-weight:700" : ""}">${
        esc(c.ticker)} ${pct(c.return_pct, 1)}</span>`).join("") +
      (r.pick_rank ? ` <span class="badge ${r.pick_was_best ? "best"
        : r.pick_rank === r.n_ranked ? "worst" : "mid"}">pick ${r.pick_rank}/${r.n_ranked}</span>` : "");
  } catch { /* leave the ellipsis */ }
}

/* ----------------------------------------------------------- track record */

async function loadScoreboard() {
  const b = await api("/api/scoreboard");
  const box = $("#scoreboard");

  if (!b.graded_trades) {
    box.innerHTML = `<div class="empty">
      No graded trades yet. Close a position that came from a saved recommendation
      and the hit rate starts here.<br><br>
      ${b.closed_trades ? `${b.closed_trades} closed trade(s) had no recommendation
        attached, so they show P&amp;L only.` : ""}</div>`;
    return;
  }

  const beat = b.hit_rate_pct - b.random_baseline_pct;
  box.innerHTML = `
    <div class="stats">
      <div class="stat"><div class="k">Hit rate</div>
        <div class="v ${beat > 0 ? "up" : beat < 0 ? "down" : ""}">${num(b.hit_rate_pct, 1)}%</div>
        <div class="n">the pick was the best of its field</div></div>
      <div class="stat"><div class="k">Random baseline</div>
        <div class="v flat">${num(b.random_baseline_pct, 1)}%</div>
        <div class="n">what coin-flipping would score</div></div>
      <div class="stat"><div class="k">Edge vs field</div>
        <div class="v ${b.avg_edge_vs_field_pct > 0 ? "up" : "down"}">${
          b.avg_edge_vs_field_pct > 0 ? "+" : ""}${num(b.avg_edge_vs_field_pct)}%</div>
        <div class="n">pick return minus holding all candidates equally</div></div>
      <div class="stat"><div class="k">Avg opportunity cost</div>
        <div class="v">${num(b.avg_opportunity_cost_pct)}%</div>
        <div class="n">given up against the best candidate</div></div>
      <div class="stat"><div class="k">Realised P&amp;L</div>
        <div class="v ${b.total_pnl > 0 ? "up" : b.total_pnl < 0 ? "down" : ""}">${
          b.total_pnl != null ? num(b.total_pnl) : "&mdash;"}</div>
        <div class="n">${b.closed_trades} closed trade(s), win rate ${
          b.win_rate_pct != null ? num(b.win_rate_pct, 0) + "%" : "&mdash;"}</div></div>
      <div class="stat"><div class="k">Graded</div>
        <div class="v">${b.graded_trades}</div>
        <div class="n">trades linked to a recommendation</div></div>
    </div>

    <div class="callout ${beat > 0 ? "good" : beat < 0 ? "bad" : "warn"}">
      ${b.graded_trades < 20
        ? `<strong>Too few trades to conclude anything.</strong> With ${b.graded_trades}
           graded trade(s), the hit rate is noise &mdash; a run of three wins is not
           evidence. Around 20&ndash;30 closed trades is where the number starts to
           mean something.`
        : beat > 0
          ? `<strong>Beating the baseline by ${num(beat, 1)} points.</strong>
             Over ${b.graded_trades} trades, the model has picked the best candidate
             more often than chance would.`
          : `<strong>Not beating the baseline.</strong> Over ${b.graded_trades} trades
             the model picked the best candidate ${num(b.hit_rate_pct, 1)}% of the time
             against ${num(b.random_baseline_pct, 1)}% for chance. Consider retuning
             the weights in <code>config.json</code>.`}
    </div>

    <div class="table-wrap" style="margin-top:16px"><table>
      <thead><tr><th>Where the pick actually finished</th><th class="num">Trades</th>
        <th class="num">Share</th></tr></thead>
      <tbody>${Object.keys(b.by_rank).sort().map(k => `<tr>
        <td>Rank ${esc(k)}</td>
        <td class="num">${b.by_rank[k]}</td>
        <td class="num">${num(100 * b.by_rank[k] / b.graded_trades, 0)}%</td>
      </tr>`).join("")}</tbody></table></div>`;
}

/* ------------------------------------------------------------------ init */

api("/api/config")
  .then(cfg => { if (cfg.compare_sessions) COMPARE_SESSIONS = cfg.compare_sessions; })
  .catch(() => { /* the built-in default is a fine fallback */ })
  .finally(() => loadHotlist().catch(e => toast(e.message, true)));
