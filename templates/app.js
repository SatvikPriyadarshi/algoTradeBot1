// ─── Quant Algo Dashboard — Frontend ───────────────────────────────────────
// Connects to the engine WebSocket and renders the live dashboard + backtest lab.
// All template literals use string concatenation to avoid JS syntax issues.

var wsUri = 'ws://' + window.location.host + '/ws';
var socket;
var reconnectTimer = null;
var equityChartInstance = null;
var wsConnected = false;

// ─── Tab Switcher ───────────────────────────────────────────────────────────
function switchTab(tabName) {
    var liveContent = document.getElementById("tab-live-content");
    var backtestContent = document.getElementById("tab-backtest-content");
    var btnLive = document.getElementById("btn-live");
    var btnBacktest = document.getElementById("btn-backtest");

    if (tabName === 'live') {
        liveContent.classList.remove("hidden");
        backtestContent.classList.add("hidden");
        btnLive.classList.add("active");
        btnBacktest.classList.remove("active");
    } else {
        liveContent.classList.add("hidden");
        backtestContent.classList.remove("hidden");
        btnLive.classList.remove("active");
        btnBacktest.classList.add("active");
        if (equityChartInstance) {
            setTimeout(function() { equityChartInstance.resize(); }, 100);
        }
    }
}

function strategyDisplayName(name) {
    var labels = {
        opt_mean_rev: "opt_mean_rev · M15 mean reversion"
    };
    return labels[name] || name || "opt_mean_rev";
}

// ─── WebSocket ──────────────────────────────────────────────────────────────
function connect() {
    console.log("[WS] Connecting to " + wsUri);
    try {
        socket = new WebSocket(wsUri);
    } catch (e) {
        console.error("[WS] Failed to create WebSocket:", e);
        return;
    }

    socket.onopen = function() {
        console.log("[WS] ✓ Connected");
        wsConnected = true;
        if (reconnectTimer) {
            clearInterval(reconnectTimer);
            reconnectTimer = null;
        }
    };

    socket.onmessage = function(event) {
        try {
            var data = JSON.parse(event.data);
            updateDashboard(data);
        } catch (e) {
            console.error("[WS] Parse error:", e);
        }
    };

    socket.onclose = function() {
        console.warn("[WS] Closed. Reconnecting in 3s...");
        wsConnected = false;
        if (!reconnectTimer) {
            reconnectTimer = setInterval(connect, 3000);
        }
    };

    socket.onerror = function(err) {
        console.error("[WS] Error:", err);
    };
}

// ─── Dashboard Update ───────────────────────────────────────────────────────
function updateDashboard(data) {
    // ── Header ──
    var symVal = document.getElementById("sym-val");
    if (symVal && data.symbols) {
        symVal.innerText = data.symbols.length + " Pairs Active";
    }

    var modeVal = document.getElementById("mode-val");
    if (modeVal && data.mode) {
        modeVal.innerText = data.mode.toUpperCase();
        if (data.mode === 'live') {
            modeVal.style.color = "var(--red-color)";
            modeVal.style.textShadow = "0 0 10px var(--red-glow)";
        } else {
            modeVal.style.color = "var(--accent-color)";
            modeVal.style.textShadow = "0 0 10px var(--accent-glow)";
        }
    }

    // ── MT5 Status Badge ──
    var statusBadge = document.getElementById("status-badge");
    var heartbeat = document.getElementById("heartbeat-ring");
    if (statusBadge && data.status) {
        statusBadge.innerText = data.status;
        if (data.status === "CONNECTED") {
            statusBadge.classList.add("connected");
            if (heartbeat) heartbeat.classList.add("active");
        } else {
            statusBadge.classList.remove("connected");
            if (heartbeat) heartbeat.classList.remove("active");
        }
    }

    // ── Balance & Equity (show whenever CONNECTED, even if 0) ──
    var balanceVal = document.getElementById("balance-val");
    var equityVal = document.getElementById("equity-val");
    var equitySub = document.getElementById("equity-sub");
    var balanceSub = document.getElementById("balance-sub");

    if (data.status === "CONNECTED") {
        balanceVal.innerText = formatCurrency(data.balance);
        equityVal.innerText = formatCurrency(data.equity);
        if (balanceSub) balanceSub.innerText = "Real-Time Sync ✓";
        updateMt5SizingReadout(data);

        var floatingPnl = data.equity - data.balance;
        var floatingPct = data.balance > 0 ? (floatingPnl / data.balance) * 100 : 0;

        if (floatingPnl >= 0) {
            equitySub.innerText = "+" + floatingPct.toFixed(2) + "% Floating P&L";
            equitySub.style.color = "var(--green-color)";
        } else {
            equitySub.innerText = floatingPct.toFixed(2) + "% Floating P&L";
            equitySub.style.color = "var(--red-color)";
        }
    } else {
        balanceVal.innerText = "—";
        equityVal.innerText = "—";
        equitySub.innerText = "Connect MT5 to sync";
        equitySub.style.color = "var(--text-muted)";
        if (balanceSub) balanceSub.innerText = "Connect MT5 to sync";
    }

    // ── Today's P&L ──
    var pnlVal = document.getElementById("pnl-val");
    var pnlCard = document.getElementById("today-pnl-card");
    if (pnlVal && pnlCard) {
        var pnl = data.pnl_today || 0;
        pnlVal.innerText = formatCurrency(Math.abs(pnl));
        if (pnl > 0) {
            pnlVal.innerText = "+" + formatCurrency(pnl);
            pnlCard.className = "metric-card glass glow-green";
            pnlVal.style.color = "var(--green-color)";
        } else if (pnl < 0) {
            pnlVal.innerText = "-" + formatCurrency(Math.abs(pnl));
            pnlCard.className = "metric-card glass glow-red";
            pnlVal.style.color = "var(--red-color)";
        } else {
            pnlVal.innerText = "0.00";
            pnlCard.className = "metric-card glass";
            pnlVal.style.color = "var(--text-primary)";
        }
    }

    // ── Bollinger Bands (live tick + M15 MA25) ──
    var levelsContainer = document.getElementById("levels-container-list");
    var assetCount = document.getElementById("levels-asset-count");
    var levelsLiveTag = document.getElementById("levels-live-tag");
    if (levelsLiveTag) {
        levelsLiveTag.innerText = data.status === "CONNECTED" ? "LIVE TICK" : "OFFLINE";
        levelsLiveTag.style.color = data.status === "CONNECTED" ? "var(--green-color)" : "var(--text-muted)";
    }
    if (levelsContainer && data.symbols && data.status === "CONNECTED") {
        if (assetCount) assetCount.innerText = data.symbols.length + " Assets";
        var html = "";
        for (var i = 0; i < data.symbols.length; i++) {
            var sym = data.symbols[i];
            var high = (data.swing_highs && data.swing_highs[sym]) ? Number(data.swing_highs[sym]).toFixed(5) : "—";
            var low = (data.swing_lows && data.swing_lows[sym]) ? Number(data.swing_lows[sym]).toFixed(5) : "—";
            var ma = (data.poc_levels && data.poc_levels[sym]) ? Number(data.poc_levels[sym]).toFixed(5) : "—";
            var price = (data.last_prices && data.last_prices[sym]) ? Number(data.last_prices[sym]).toFixed(5) : "—";
            var maNum = data.poc_levels && data.poc_levels[sym] ? Number(data.poc_levels[sym]) : null;
            var priceNum = data.last_prices && data.last_prices[sym] ? Number(data.last_prices[sym]) : null;
            var vsMa = "";
            if (maNum && priceNum) {
                var diffPips = (priceNum - maNum) * (sym.indexOf("JPY") >= 0 ? 100 : 10000);
                vsMa = (diffPips >= 0 ? "+" : "") + diffPips.toFixed(1) + " pips vs MA";
            }
            var updated = "";
            if (data.bands_updated_at && data.bands_updated_at[sym]) {
                var ageSec = Math.max(0, Math.round(Date.now() / 1000 - data.bands_updated_at[sym]));
                updated = ageSec < 3 ? "just now" : ageSec + "s ago";
            }
            html += '<div class="level-row">' +
                '<span class="level-sym">' + sym + '</span>' +
                '<div class="level-pair">' +
                '<span class="level-tag price-tag">Bid ' + price + '</span>' +
                '<span class="level-tag high-tag">+2σ ' + high + '</span>' +
                '<span class="level-tag">MA ' + ma + '</span>' +
                '<span class="level-tag low-tag">-2σ ' + low + '</span>' +
                '</div>' +
                (vsMa ? '<span class="level-meta">' + vsMa + ' · ' + updated + '</span>' : '') +
                '</div>';
        }
        levelsContainer.innerHTML = html;
    }

    // ── Active Positions ──
    var noPosPanel = document.getElementById("no-position-msg");
    var posContainer = document.getElementById("active-positions-container");
    var posActiveDot = document.getElementById("position-active-dot");

    if (data.active_positions && posContainer) {
        var hasActive = false;
        var posHTML = "";

        for (var sym in data.active_positions) {
            var pos = data.active_positions[sym];
            if (pos !== null && pos !== undefined) {
                hasActive = true;
                var isBuy = pos.action === "BUY";
                var pnlClass = pos.net_pnl >= 0 ? "profit" : "loss";
                var pnlSign = pos.net_pnl >= 0 ? "+" : "-";
                var pnlAbs = Math.abs(pos.net_pnl).toFixed(2);

                var modeLabel = (data.mode === "live") ? "LIVE" : "PAPER";
                var modeCls = (data.mode === "live") ? "live" : "dry_run";

                posHTML += '<div class="position-details">' +
                    '<div class="pos-header-row">' +
                    '<span class="pos-symbol">' + sym + '</span>' +
                    '<span class="mode-pill ' + modeCls + '">' + modeLabel + '</span>' +
                    '<div class="pos-direction-tag ' + (isBuy ? '' : 'sell') + '">' + pos.action + '</div>' +
                    '</div>' +
                    '<div class="pos-grid">' +
                    '<div class="pos-item"><span class="lbl">Entry</span><span class="val">' + Number(pos.entry_price).toFixed(5) + '</span></div>' +
                    '<div class="pos-item"><span class="lbl">Size</span><span class="val">' + Number(pos.size).toFixed(2) + '</span></div>' +
                    '<div class="pos-item"><span class="lbl">SL</span><span class="val">' + Number(pos.sl).toFixed(5) + '</span></div>' +
                    '<div class="pos-item"><span class="lbl">TP</span><span class="val">' + Number(pos.tp).toFixed(5) + '</span></div>' +
                    '<div class="pos-item"><span class="lbl">Open for</span><span class="val">' + (pos.elapsed_label || "—") + '</span></div>' +
                    '<div class="pos-item"><span class="lbl">Bars held</span><span class="val">' + (pos.bars_held || 0) + '</span></div>' +
                    '</div>' +
                    '<div class="floating-pnl-container">' +
                    '<span class="pnl-lbl">Floating P&L' + (data.mode === 'live' ? '' : ' (simulated)') + '</span>' +
                    '<span class="pnl-val ' + pnlClass + '">' + pnlSign + '$' + pnlAbs + '</span>' +
                    '</div>' +
                    '</div>';
            }
        }

        if (hasActive) {
            noPosPanel.classList.add("hidden");
            posContainer.innerHTML = posHTML;
            posActiveDot.classList.add("green");
        } else {
            noPosPanel.classList.remove("hidden");
            posContainer.innerHTML = "";
            posActiveDot.classList.remove("green");
        }
    }

    // ── Strategy & timeframe ──
    var stratVal = document.getElementById("strategy-val");
    if (stratVal && data.strategy) {
        stratVal.innerText = data.strategy;
    }
    var logoStrat = document.getElementById("logo-strategy");
    if (logoStrat && data.strategy) {
        logoStrat.innerText = strategyDisplayName(data.strategy);
    }
    if (data.strategy) {
        document.title = "Quant Algo — " + data.strategy;
    }
    var badge = document.getElementById("live-timeframe-badge");
    if (badge && data.symbols) {
        var tf = data.timeframe || "M15";
        badge.innerText = "SCANNING " + data.symbols.length + " PAIRS · " + tf;
    }
    var cfgBadge = document.getElementById("config-tf-badge");
    if (cfgBadge) {
        cfgBadge.innerText = (data.timeframe || "M15") + " · " + (data.strategy || "opt_mean_rev");
    }

    // ── Pipeline ──
    if (data.symbols && data.symbols.length > 0 && data.rules) {
        var pipeSym = data.symbols[0];
        if (data.active_positions) {
            for (var j = 0; j < data.symbols.length; j++) {
                if (data.active_positions[data.symbols[j]] !== null && data.active_positions[data.symbols[j]] !== undefined) {
                    pipeSym = data.symbols[j];
                    break;
                }
            }
        }
        if (data.rules[pipeSym]) {
            updatePipelineUI(data.rules[pipeSym]);
        }
    }

    if (data.calendar) drawCalendar(data.calendar);
    if (data.trade_stats) updateTradeStats(data.trade_stats, data.trades_total);
    if (data.trades) updateHistoryTable(data.trades, data.trades_total);
    if (data.account_name || data.balance != null) updateJournalAccountLabel(data);
}

function updateJournalAccountLabel(data) {
    var el = document.getElementById("journal-account-label");
    if (!el) return;
    var name = data.account_name || "MT5";
    var bal = data.balance != null ? " · $" + Number(data.balance).toFixed(2) : "";
    el.innerText = "Live · " + name + bal;
}

// ─── Pipeline UI ────────────────────────────────────────────────────────────
function updatePipelineUI(rules) {
    if (!rules) return;

    var steps = ["atr", "band", "stops"];
    var states = [
        rules.atr_ok,
        rules.band_signal,
        rules.stops_set
    ];

    for (var i = 0; i < steps.length; i++) {
        var stepEl = document.getElementById("step-" + steps[i]);
        var dotEl = document.getElementById("dot-" + steps[i]);
        if (!stepEl || !dotEl) continue;

        if (states[i]) {
            stepEl.className = "pipeline-step passed";
            dotEl.className = "pipeline-dot passed";
        } else if (i === 0 || states[i - 1]) {
            stepEl.className = "pipeline-step active";
            dotEl.className = "pipeline-dot active";
        } else {
            stepEl.className = "pipeline-step";
            dotEl.className = "pipeline-dot";
        }
    }
}

// ─── Calendar Heatmap ───────────────────────────────────────────────────────
function drawCalendar(calendarData) {
    var grid = document.getElementById("calendar-grid-container");
    if (!grid) return;
    grid.innerHTML = "";

    var today = new Date();
    for (var i = 29; i >= 0; i--) {
        var d = new Date();
        d.setDate(today.getDate() - i);
        var dateStr = formatDateKey(d);
        var dayPnl = calendarData[dateStr] || 0.0;

        var dayBox = document.createElement("div");
        dayBox.className = "cal-day";
        dayBox.title = dateStr + ": " + (dayPnl === 0 ? "No trade" : (dayPnl > 0 ? "+" : "") + "$" + dayPnl.toFixed(2));

        if (dayPnl > 0) dayBox.classList.add("profit");
        else if (dayPnl < 0) dayBox.classList.add("loss");

        var dayNum = document.createElement("span");
        dayNum.className = "day-num";
        dayNum.innerText = d.getDate();
        dayBox.appendChild(dayNum);
        grid.appendChild(dayBox);
    }
}

// ─── Trade Journal ──────────────────────────────────────────────────────────
function formatShortTime(ts) {
    if (!ts) return "—";
    return String(ts).replace("T", " ").slice(0, 16);
}

function exitBadgeHtml(trade) {
    var et = trade.exit_type || "OTHER";
    var label = et;
    if (trade.result) {
        if (trade.result.indexOf("STOP") >= 0) label = "SL";
        else if (trade.result.indexOf("TARGET") >= 0 || trade.result.indexOf("MEAN") >= 0) label = "TP";
        else if (trade.result.indexOf("TIME") >= 0) label = "TIME";
        else label = trade.result.length > 12 ? trade.result.slice(0, 12) : trade.result;
    }
    var cls = "exit-other";
    if (et === "TP" || label === "TP") cls = "exit-tp";
    else if (et === "SL" || label === "SL") cls = "exit-sl";
    else if (et === "TIME" || label === "TIME") cls = "exit-time";
    return '<span class="exit-badge ' + cls + '">' + label + '</span>';
}

function updateTradeStats(stats, total) {
    if (!stats) return;
    var totalCount = total != null ? total : stats.total_trades;
    var badge = document.getElementById("total-trades-badge");
    if (badge) badge.innerText = totalCount + " Trades";

    var wr = document.getElementById("js-win-rate");
    if (wr) wr.innerText = stats.total_trades ? stats.win_rate + "% (" + stats.wins + "W / " + stats.losses + "L)" : "—";

    var net = document.getElementById("js-net-pnl");
    if (net) {
        var n = stats.net_pnl || 0;
        net.innerText = (n >= 0 ? "+" : "") + "$" + Number(n).toFixed(2);
        net.style.color = n >= 0 ? "var(--green-color)" : "var(--red-color)";
    }

    var dur = document.getElementById("js-avg-duration");
    if (dur) dur.innerText = stats.avg_duration_label || "—";

    var pf = document.getElementById("js-profit-factor");
    if (pf) pf.innerText = stats.total_trades ? String(stats.profit_factor) : "—";

    var mix = document.getElementById("js-exit-mix");
    if (mix) mix.innerText = stats.total_trades
        ? stats.tp_exits + " / " + stats.sl_exits + " / " + stats.time_exits
        : "—";

    var bars = document.getElementById("js-avg-bars");
    if (bars) bars.innerText = stats.total_trades ? String(stats.avg_bars_held) : "—";
}

function updateHistoryTable(trades, total) {
    var rowsContainer = document.getElementById("trade-history-rows");
    if (!rowsContainer) return;
    rowsContainer.innerHTML = "";

    var badge = document.getElementById("total-trades-badge");
    if (badge) badge.innerText = (total != null ? total : trades.length) + " Trades";

    if (!trades || trades.length === 0) {
        rowsContainer.innerHTML = '<tr><td colspan="11" class="empty-table-msg">No live MT5 trades yet.</td></tr>';
        return;
    }

    var displayTrades = trades.slice().reverse();
    for (var i = 0; i < displayTrades.length; i++) {
        var trade = displayTrades[i];
        var tr = document.createElement("tr");
        var isBuy = trade.action === "BUY";
        var isWin = Number(trade.net_pnl) > 0;

        tr.innerHTML =
            '<td>' + (trade.trade_id || "—") + '</td>' +
            '<td>' + formatShortTime(trade.entry_time) + '</td>' +
            '<td>' + formatShortTime(trade.exit_time) + '</td>' +
            '<td>' + (trade.duration_label || "—") + '</td>' +
            '<td>' + (trade.symbol || "—") + '</td>' +
            '<td><span class="tag-action ' + (isBuy ? "buy" : "sell") + '">' + trade.action + '</span></td>' +
            '<td>' + Number(trade.size).toFixed(2) + '</td>' +
            '<td>' + Number(trade.entry_price).toFixed(5) + '</td>' +
            '<td>' + (trade.exit_price ? Number(trade.exit_price).toFixed(5) : "—") + '</td>' +
            '<td>' + exitBadgeHtml(trade) + '</td>' +
            '<td><span class="pnl-text ' + (isWin ? "win" : "loss") + '">' + (isWin ? "+" : "") + Number(trade.net_pnl).toFixed(2) + '</span></td>';
        rowsContainer.appendChild(tr);
    }
}

function populateJournalSymbolFilter(symbols) {
    var sel = document.getElementById("journal-filter-symbol");
    if (!sel || !symbols) return;
    var current = sel.value;
    sel.innerHTML = '<option value="">All symbols</option>';
    for (var i = 0; i < symbols.length; i++) {
        var opt = document.createElement("option");
        opt.value = symbols[i];
        opt.innerText = symbols[i];
        sel.appendChild(opt);
    }
    if (current) sel.value = current;
}

function loadTradeJournal() {
    var sym = document.getElementById("journal-filter-symbol");
    var exitF = document.getElementById("journal-filter-exit");
    var params = new URLSearchParams();
    params.set("limit", "500");
    if (sym && sym.value) params.set("symbol", sym.value);
    if (exitF && exitF.value) params.set("exit_type", exitF.value);

    fetch("/api/trades?" + params.toString())
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res.status === "OK") {
                updateTradeStats(res.stats, res.total);
                updateHistoryTable(res.trades, res.total);
            }
        })
        .catch(function(e) { console.warn("Journal load failed", e); });
}

function bindJournalFilters() {
    ["journal-filter-symbol", "journal-filter-exit"].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener("change", loadTradeJournal);
    });
}

// ─── Engine config (.env) ───────────────────────────────────────────────────
function applyConfigToForms(cfg) {
    if (!cfg) return;
    var liveRisk = document.getElementById("live-risk");
    var btRisk = document.getElementById("bt-risk");
    var btCap = document.getElementById("bt-capital");
    var btLev = document.getElementById("bt-leverage");
    var btTf = document.getElementById("bt-timeframe");
    var symEl = document.getElementById("config-symbols");

    if (liveRisk) liveRisk.value = cfg.risk_per_trade;
    if (btRisk) btRisk.value = cfg.risk_per_trade;
    if (btCap) btCap.value = cfg.backtest_trade_capital || cfg.trade_capital || 500;
    if (btLev) btLev.value = cfg.backtest_leverage || cfg.leverage || 100;
    if (btTf && cfg.timeframe) btTf.value = cfg.timeframe;
    if (symEl && cfg.symbols) {
        symEl.innerText = cfg.symbols.join(", ");
        populateJournalSymbolFilter(cfg.symbols);
    }
    var modeEl = document.getElementById("config-engine-mode");
    if (modeEl) modeEl.innerText = (cfg.engine_mode || cfg.mode || "dry_run").toUpperCase();

    var balEl = document.getElementById("cfg-balance");
    var marginEl = document.getElementById("cfg-margin-free");
    var levEl = document.getElementById("cfg-leverage");
    if (balEl && cfg.balance) balEl.innerText = "$" + Number(cfg.balance).toFixed(2);
    if (marginEl && cfg.margin_free != null) marginEl.innerText = "$" + Number(cfg.margin_free).toFixed(2);
    if (levEl && cfg.account_leverage) levEl.innerText = "1:" + cfg.account_leverage;
}

function updateMt5SizingReadout(data) {
    var balEl = document.getElementById("cfg-balance");
    var marginEl = document.getElementById("cfg-margin-free");
    var levEl = document.getElementById("cfg-leverage");
    if (balEl && data.balance != null) balEl.innerText = "$" + Number(data.balance).toFixed(2);
    if (marginEl && data.margin_free != null) marginEl.innerText = "$" + Number(data.margin_free).toFixed(2);
    if (levEl && data.account_leverage) levEl.innerText = "1:" + data.account_leverage;
}

function loadEngineConfig() {
    fetch("/api/config")
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res.status === "OK" && res.config) {
                applyConfigToForms(res.config);
            }
        })
        .catch(function(e) { console.warn("Config load failed", e); });
}

document.getElementById("risk-config-form").addEventListener("submit", function(e) {
    e.preventDefault();
    var feedback = document.getElementById("risk-config-feedback");
    var risk = parseFloat(document.getElementById("live-risk").value);

    if (risk <= 0) {
        if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ Enter a positive risk amount.</span>';
        return;
    }

    fetch("/api/config/risk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ risk_per_trade: risk })
    })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(res) {
        if (res.ok && res.data.status === "OK") {
            applyConfigToForms(res.data.config);
            if (feedback) feedback.innerHTML = '<span style="color:var(--green-color)">✓ ' + res.data.message + '</span>';
        } else {
            if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ ' + (res.data.message || "Save failed") + '</span>';
        }
    })
    .catch(function() {
        if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ Could not save config.</span>';
    });
});

// ─── Form: Backtest ─────────────────────────────────────────────────────────
document.getElementById("backtest-form").addEventListener("submit", function(e) {
    e.preventDefault();

    var runBtn = document.getElementById("run-backtest-btn");
    var btnText = document.getElementById("bt-btn-text");
    var feedback = document.getElementById("bt-feedback");
    var symbol = document.getElementById("bt-symbol").value;
    var timeframe = document.getElementById("bt-timeframe").value;
    var bars = parseInt(document.getElementById("bt-bars").value);
    var balance = parseFloat(document.getElementById("bt-balance").value);
    var tradeCapital = parseFloat(document.getElementById("bt-capital").value);
    var maxRisk = parseFloat(document.getElementById("bt-risk").value);
    var leverage = parseFloat(document.getElementById("bt-leverage").value);

    if (!maxRisk || maxRisk <= 0) {
        if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ Enter a valid max risk amount.</span>';
        return;
    }
    if (!tradeCapital || tradeCapital <= 0) {
        if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ Enter valid trade capital.</span>';
        return;
    }
    if (maxRisk > tradeCapital) {
        if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ Max risk cannot exceed trade capital.</span>';
        return;
    }
    if (tradeCapital > balance) {
        if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ Trade capital cannot exceed starting balance.</span>';
        return;
    }

    btnText.innerText = "DOWNLOADING DATA & SIMULATING...";
    runBtn.disabled = true;
    if (feedback) feedback.innerHTML = '<span style="color:var(--accent-color)">Simulating ' + symbol + ' — $' + tradeCapital + ' capital, $' + maxRisk + ' max risk/trade...</span>';

    fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            symbol: symbol, timeframe: timeframe, bars: bars,
            initial_balance: balance, trade_capital: tradeCapital,
            max_risk: maxRisk, leverage: leverage
        })
    })
    .then(function(response) { return response.json().then(function(d) { return { ok: response.ok, data: d }; }); })
    .then(function(res) {
        btnText.innerText = "Run Backtest Simulation";
        runBtn.disabled = false;

        if (res.ok && res.data.status === "SUCCESS") {
            if (feedback) feedback.innerHTML = '<span style="color:var(--green-color)">✓ Backtest complete! ' + res.data.summary.total_trades + ' trades executed.</span>';
            updateBacktestUI(res.data);
        } else {
            if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ ' + (res.data.message || 'Unknown error') + '</span>';
        }
    })
    .catch(function(err) {
        console.error("Backtest error:", err);
        btnText.innerText = "Run Backtest Simulation";
        runBtn.disabled = false;
        if (feedback) feedback.innerHTML = '<span style="color:var(--red-color)">✗ Server error during backtesting.</span>';
    });
});

// ─── Backtest UI ────────────────────────────────────────────────────────────
function updateBacktestUI(data) {
    var profitEl = document.getElementById("bt-profit-val");
    profitEl.innerText = formatCurrency(data.summary.net_profit);

    var returnPct = document.getElementById("bt-return-pct");
    returnPct.innerText = (data.summary.return_pct > 0 ? '+' : '') + data.summary.return_pct.toFixed(2) + "% Net";

    if (data.summary.net_profit >= 0) {
        profitEl.style.color = "var(--green-color)";
        returnPct.style.color = "var(--green-color)";
    } else {
        profitEl.style.color = "var(--red-color)";
        returnPct.style.color = "var(--red-color)";
    }

    document.getElementById("bt-winrate-val").innerText = data.summary.win_rate.toFixed(2);
    var tradesSub = data.summary.total_trades + " Closed Trades";
    if (data.summary.avg_trades_per_day != null) {
        tradesSub += " | ~" + data.summary.avg_trades_per_day + "/day";
    }
    document.getElementById("bt-trades-count").innerText = tradesSub;
    document.getElementById("bt-factor-val").innerText = data.summary.profit_factor.toFixed(2);
    document.getElementById("bt-drawdown-val").innerText = "Max DD: " + data.summary.max_drawdown.toFixed(2) + "%";

    // Extended stats
    var extStats = document.getElementById("bt-extra-stats");
    if (extStats) {
        extStats.style.display = "block";
        document.getElementById("bt-final-balance").innerText = "$" + formatCurrency(data.summary.final_balance || 0);
        document.getElementById("bt-total-count").innerText = data.summary.total_trades;

        // Calculate avg win/loss from trades
        var totalWin = 0, winCount = 0, totalLoss = 0, lossCount = 0;
        for (var i = 0; i < data.trades.length; i++) {
            if (data.trades[i].net_pnl > 0) {
                totalWin += data.trades[i].net_pnl;
                winCount++;
            } else {
                totalLoss += Math.abs(data.trades[i].net_pnl);
                lossCount++;
            }
        }
        document.getElementById("bt-avg-win").innerText = "$" + (winCount > 0 ? formatCurrency(totalWin / winCount) : "0.00");
        document.getElementById("bt-avg-loss").innerText = "$" + (lossCount > 0 ? formatCurrency(totalLoss / lossCount) : "0.00");
    }

    // Trade table
    var tableBody = document.getElementById("bt-history-rows");
    tableBody.innerHTML = "";
    document.getElementById("bt-total-badge").innerText = data.trades.length + " Trades";

    if (data.trades.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="7" class="empty-table-msg">Zero trades. Try relaxing strategy thresholds.</td></tr>';
    } else {
        var displayTrades = data.trades.slice().reverse();
        for (var j = 0; j < displayTrades.length; j++) {
            var trade = displayTrades[j];
            var tr = document.createElement("tr");
            tr.title = trade.reason || "";
            var isBuy = trade.action === "BUY";
            var isWin = trade.net_pnl > 0;

            tr.innerHTML =
                '<td>' + (trade.entry_time || '-') + '</td>' +
                '<td><span class="tag-action ' + (isBuy ? 'buy' : 'sell') + '">' + trade.action + '</span></td>' +
                '<td>' + Number(trade.size).toFixed(2) + '</td>' +
                '<td>' + Number(trade.entry_price).toFixed(5) + '</td>' +
                '<td>' + Number(trade.exit_price).toFixed(5) + '</td>' +
                '<td>' + (trade.result || '-') + '</td>' +
                '<td><span class="pnl-text ' + (isWin ? 'win' : 'loss') + '">' + (isWin ? '+' : '') + Number(trade.net_pnl).toFixed(2) + '</span></td>';
            tableBody.appendChild(tr);
        }
    }

    renderChart(data.equity);
}

// ─── Equity Chart ───────────────────────────────────────────────────────────
function renderChart(equityPoints) {
    var ctx = document.getElementById("equity-chart").getContext("2d");
    if (equityChartInstance) equityChartInstance.destroy();

    var labels = [];
    var dataPoints = [];
    for (var i = 0; i < equityPoints.length; i++) {
        labels.push(i);
        dataPoints.push(equityPoints[i].equity);
    }

    var gradient = ctx.createLinearGradient(0, 0, 0, 250);
    gradient.addColorStop(0, 'rgba(99, 102, 241, 0.5)');
    gradient.addColorStop(1, 'rgba(99, 102, 241, 0.0)');

    equityChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Equity ($)',
                data: dataPoints,
                borderColor: '#6366f1',
                borderWidth: 2.5,
                backgroundColor: gradient,
                fill: true,
                tension: 0.2,
                pointRadius: 0,
                pointHitRadius: 10
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { display: false, grid: { display: false } },
                y: {
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    ticks: { color: '#94a3b8', font: { size: 10, family: 'Space Grotesk' } }
                }
            }
        }
    });
}

// ─── Helpers ────────────────────────────────────────────────────────────────
function formatCurrency(val) {
    if (val === undefined || val === null || isNaN(val)) return "0.00";
    return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(val);
}

function formatDateKey(dateObj) {
    var yyyy = dateObj.getFullYear();
    var mm = String(dateObj.getMonth() + 1).padStart(2, '0');
    var dd = String(dateObj.getDate()).padStart(2, '0');
    return yyyy + '-' + mm + '-' + dd;
}

// ─── Boot ───────────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", function() {
    console.log("[Quant Algo] Dashboard loaded.");
    loadEngineConfig();
    bindJournalFilters();
    loadTradeJournal();
    connect();
});
