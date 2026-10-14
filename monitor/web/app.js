"use strict";
const byId = id => document.getElementById(id);
const date = ts => ts == null ? "—" : new Date(ts * 1000).toLocaleString("ja-JP", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"});
const pct = n => n == null ? "—" : `${n.toFixed(1)}%`;
const bytes = n => {
  if (n == null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)}\u00a0${units[i]}`;
};
const duration = seconds => {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)}時間${minutes % 60}分` : `${minutes}分`;
};
let lastData = null;
let requestNumber = 0;
let refreshTimer = null;

function table(id, rows, empty) {
  const target = byId(id);
  target.replaceChildren();
  if (!rows.length) rows = [[empty]];
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const text of row) {
      const td = document.createElement("td");
      td.textContent = text; // Player names are untrusted log data.
      if (row.length === 1) td.colSpan = id === "events" ? 3 : 4;
      tr.append(td);
    }
    target.append(tr);
  }
}

function chart(id, points, series, max, now, hours) {
  const canvas = byId(id), width = canvas.clientWidth, height = 210;
  const scale = window.devicePixelRatio || 1;
  canvas.width = width * scale; canvas.height = height * scale;
  const ctx = canvas.getContext("2d"); ctx.scale(scale, scale);
  const pad = {left: 35, right: 12, top: 12, bottom: 30};
  const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
  const start = now - hours * 3600;
  const x = ts => pad.left + (ts - start) / (now - start) * plotW;
  const y = value => pad.top + (1 - value / max) * plotH;
  ctx.font = "11px system-ui";
  const gridSteps = max <= 4 ? max : 4;
  for (let i = 0; i <= gridSteps; i++) {
    const value = max * i / gridSteps;
    ctx.strokeStyle = "#34483d"; ctx.beginPath(); ctx.moveTo(pad.left, y(value)); ctx.lineTo(width - pad.right, y(value)); ctx.stroke();
    ctx.fillStyle = "#a3b5aa"; ctx.textAlign = "right"; ctx.fillText(Number.isInteger(value) ? value : value.toFixed(1), pad.left - 7, y(value) + 4);
  }
  ctx.fillStyle = "#a3b5aa";
  for (let i = 0; i <= 2; i++) {
    const ts = start + (now - start) * i / 2;
    const label = new Date(ts * 1000).toLocaleString("ja-JP", hours > 24 ? {month: "numeric", day: "numeric"} : {hour: "2-digit", minute: "2-digit"});
    ctx.textAlign = i === 0 ? "left" : i === 2 ? "right" : "center";
    ctx.fillText(label, x(ts), height - 7);
  }
  for (const {get, color} of series) {
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    let previous = null;
    for (const point of points) {
      const value = get(point);
      if (value == null) { previous = null; continue; }
      // Gaps in collection should remain visible, including downsampled 7-day views.
      if (!previous || point.ts - previous.ts > Math.max(lastData.interval * 3, hours * 3600 / 480 * 3)) ctx.moveTo(x(point.ts), y(value));
      else ctx.lineTo(x(point.ts), y(value));
      previous = point;
    }
    ctx.stroke();
    if (points.length === 1 && get(points[0]) != null) { ctx.beginPath(); ctx.arc(x(points[0].ts), y(get(points[0])), 3, 0, Math.PI * 2); ctx.fill(); }
  }
  if (!points.length) { ctx.fillStyle = "#a3b5aa"; ctx.textAlign = "center"; ctx.fillText("データを収集中です", width / 2, height / 2); }
}

function drawCharts() {
  if (!lastData) return;
  const hours = Number(byId("hours").value);
  chart("load-chart", lastData.metrics, [{get: p => p.cpu_pct, color: "#93e9ad"}, {get: p => p.memory_used / p.memory_total * 100, color: "#8ec6ee"}], 100, lastData.now, hours);
  const max = Math.max(1, ...lastData.metrics.map(p => p.players || 0));
  chart("players-chart", lastData.metrics, [{get: p => p.players, color: "#eec07b"}], max, lastData.now, hours);
}

function render(data) {
  lastData = data;
  const current = data.current || {};
  const state = data.service.active_state;
  byId("state").textContent = state === "active" ? "稼働中" : state === "unknown" ? "状態不明" : "停止中";
  byId("state").className = "badge " + (state === "active" ? "active" : "stopped");
  byId("version").textContent = `バージョン ${data.version || "—"}`;
  byId("updated").textContent = `最終収集 ${date(current.ts)} / ${data.interval}秒間隔`;
  const notices = [];
  const stale = !current.ts || data.now - current.ts > data.interval * 3;
  const journalOK = data.journal.state === "ok" && data.journal.last_success && data.now - data.journal.last_success <= data.interval * 3;
  if (data.metric_error || stale) notices.push("負荷データを取得できていません。監視サービスのログを確認してください。");
  if (!journalOK) notices.push("参加ログを取得できていません。現在の参加者は確認できません。");
  if (!data.roster_complete) notices.push("現在の起動開始までのログが揃っていないため、参加者の一部を表示できない可能性があります。");
  byId("error").hidden = !notices.length;
  byId("error").textContent = notices.join(" ");
  byId("cpu").textContent = pct(current.cpu_pct);
  byId("mc-cpu").textContent = `Bedrock ${pct(current.mc_cpu_pct)}`;
  byId("memory").textContent = current.memory_total ? `${bytes(current.memory_used)} / ${bytes(current.memory_total)}` : "—";
  byId("mc-memory").textContent = `Bedrock ${bytes(current.mc_rss)} / 使用可能 ${bytes(current.memory_available)}`;
  byId("swap").textContent = current.swap_total == null ? "—" : `${bytes(current.swap_used)} / ${bytes(current.swap_total)}`;
  byId("swap-io").textContent = `読込 ${bytes(current.swap_in_bps)}/s / 書込 ${bytes(current.swap_out_bps)}/s`;
  byId("disk").textContent = bytes(current.disk_free);
  byId("disk-total").textContent = `容量 ${bytes(current.disk_total)}`;
  byId("restarts").textContent = `自動再起動 ${data.service.automatic_restarts ?? "—"}回（systemdの現在のカウンター）`;
  const rosterKnown = journalOK && state !== "unknown";
  const online = rosterKnown && state === "active" ? data.online : [];
  byId("online-count").textContent = rosterKnown ? `${online.length}人${data.roster_complete ? "" : "（確認できた人数）"}` : "不明";
  byId("roster-note").textContent = !rosterKnown ? "ログ取得が復旧するまで人数の表示を保留します。" : online.length ? "参加ログから推定しています。" : "確認できた参加者はいません。";
  byId("online").replaceChildren();
  for (const player of online) {
    const li = document.createElement("li"), name = document.createElement("strong"), detail = document.createElement("span");
    name.textContent = player.name; detail.className = "muted";
    detail.textContent = `${date(player.joined)}から / ${duration(data.now - player.joined)}`;
    li.append(name, detail); byId("online").append(li);
  }
  const kinds = {join: "参加", leave: "退出", start: "サーバー起動"};
  table("events", data.events.map(e => [date(e.ts), e.name || "—", kinds[e.kind] || e.kind]), "まだ履歴がありません。");
  const reasons = {leave: "退出", restart: "再起動 ※", stopped: "停止 ※", log_gap: "ログ欠落 ※", reconnect: "再接続 ※"};
  table("sessions", data.sessions.map(s => [date(s.joined), s.name, duration((s.ended ?? data.now) - s.joined), s.ended == null ? (rosterKnown ? "接続中（推定）" : "確認待ち") : reasons[s.reason] || "終了 ※"]), "まだ記録がありません。");
  drawCharts();
}

async function refresh() {
  clearTimeout(refreshTimer);
  const number = ++requestNumber;
  try {
    const response = await fetch(`api/status?hours=${byId("hours").value}`, {cache: "no-store", signal: AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (number === requestNumber) render(data);
  } catch (error) {
    if (number === requestNumber) {
      byId("error").hidden = false;
      byId("error").textContent = "監視画面に接続できません。監視サービスを確認してください。表示中のデータは前回取得したものです。";
      byId("state").textContent = "接続できません";
      byId("online-count").textContent = "不明";
      byId("online").replaceChildren();
    }
  } finally {
    if (number === requestNumber) refreshTimer = setTimeout(refresh, 15000);
  }
}
byId("hours").addEventListener("change", refresh);
window.addEventListener("resize", drawCharts);
refresh();
