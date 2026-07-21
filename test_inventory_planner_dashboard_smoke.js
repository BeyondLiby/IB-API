const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const html = fs.readFileSync("sell_side_inventory_planner.html", "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/);
assert(script, "script not found");
assert(!html.includes('id="stressTable"'), "pressure test table should be removed");
assert(!html.includes("压力场景"), "pressure test section should be removed");
assert(html.includes('id="activeUnderlying"'), "product selector missing");
assert(html.includes('id="refreshDefault"'), "refresh data button missing");
assert(html.includes('id="refreshFull"'), "full refresh button missing");
assert(html.includes('id="refreshScheduled"'), "US/Eastern date-aware refresh button missing");
assert(html.includes('id="refreshProgress"'), "refresh progress bar missing");
assert(html.includes('id="refreshDuration"'), "per-refresh duration display missing");
assert(html.includes('id="refreshPositionsDuration"'), "position quote timing missing");
assert(html.includes('id="refreshOptionsDuration"'), "option refresh timing missing");
assert(html.includes('id="refreshFuturesDuration"'), "futures refresh timing missing");
assert(html.includes('id="refreshOtherDuration"'), "other refresh timing missing");
assert(html.includes('id="refreshLogToggle"'), "raw/summary log toggle missing");
assert(html.includes('id="refreshLogCopy"'), "copy-log button missing");
assert(/Delta 预警阈值[\s\S]*id="highDeltaPremiumThreshold"/.test(html), "visible Delta warning threshold missing");
assert(/等效期货 Delta ≥[\s\S]*id="equivalentFutureDeltaThreshold"/.test(html), "equivalent-future delta parameter missing");
assert(/最小实值 ticks[\s\S]*id="equivalentFutureMinIntrinsicTicks"/.test(html), "equivalent-future intrinsic-value parameter missing");
assert(/最大时间价值 ticks[\s\S]*id="equivalentFutureMaxTimeValueTicks"/.test(html), "equivalent-future time-value parameter missing");
assert(/id="equivalentFutureDeltaThreshold"[^>]*value="0\.90"/.test(html), "equivalent-future delta should default to 0.90");
assert(/id="equivalentFutureMinIntrinsicTicks"[^>]*value="1"/.test(html), "minimum intrinsic value should default to one tick");
assert(/id="equivalentFutureMaxTimeValueTicks"[^>]*value="2"/.test(html), "maximum time value should default to two ticks");
assert(!html.includes('id="positionFile"'), "retired positions upload should not remain in the header");
assert(!html.includes('id="chainFile"'), "retired chain upload should not remain in the header");
assert(!html.includes('id="barsFile"'), "retired bars upload should not remain in the header");
assert(!html.includes('id="loadSample"'), "retired sample loader should not remain in the header");
assert(!html.includes('id="exportJson"') && !html.includes('id="exportCsv"') && !html.includes('id="exportMarkdown"'), "retired export controls should not remain in the header");
assert(!html.includes("链表颜色"), "retired chain color control should not remain visible");
assert(html.includes('id="refreshMonthZF"'), "ZF refresh contract selector missing");
assert(html.includes('id="refreshMonthZN"'), "ZN refresh contract selector missing");
assert(html.includes('id="refreshMonthZC"'), "ZC refresh contract selector missing");
assert(!html.includes('id="putZonePreset"'), "old put zone dropdown should be removed");
assert(html.includes('id="inventoryChain"'), "inventory option-chain view missing");
assert(html.includes('id="productOverview"'), "product overview missing");
assert(html.includes('id="portfolioQuoteStatus"'), "portfolio quote timestamp/status missing beside the overview title");
assert(html.includes('const LIVE_POSITIONS_PATH = "/api/live-positions"'), "live-position polling endpoint missing");
assert(html.includes("深度ITM"), "deep-ITM future-equivalent identification missing");
assert(html.includes('id="portfolioPremiumSummary"'), "portfolio premium summary missing");
assert(html.includes('id="premiumExpiryTable"'), "premium by-expiry table missing");
assert(html.includes('id="premiumCurveChart"'), "cumulative premium chart missing");
assert(html.includes('id="premiumCurveRawTotal"'), "raw premium curve legend total missing");
assert(html.includes('id="premiumCurveWeightedTotal"'), "weighted premium curve legend total missing");
assert(html.includes('id="premiumCurveTooltip"'), "interactive premium curve tooltip missing");
assert(html.includes('class="panel premium-overview-layout"'), "premium summary, expiry overview, and cumulative chart should share one responsive panel");
assert(html.includes('id="highDeltaPremiumThreshold"'), "configurable delta-weighted premium threshold missing");
assert(/id="highDeltaPremiumThreshold"[^>]*value="0\.40"/.test(html), "delta-weighted premium threshold should default to 0.40");
assert(html.includes('<section class="planner-state" hidden aria-hidden="true">'), "planner defaults should remain available as hidden internal state");
assert(!html.includes("用户假设与交易区域"), "retired user-assumption panel should no longer be visible");
assert(!html.includes("<h2>目标压力</h2>"), "retired target-pressure panel should no longer be visible");
assert(/<section class="panel">\s*<div id="productOverview" class="product-overview"><\/div>\s*<\/section>\s*<div class="section-heading">\s*<h2>底层资产走势<\/h2>/.test(html), "product overview should sit immediately above underlying charts");
assert(html.includes('id="greekDteTable"'), "DTE greek table missing");
assert(html.includes('id="dailyPriceChart"'), "daily future price chart missing");
assert(html.includes('id="intradayPriceChart"'), "intraday future price chart missing");
assert(html.includes('id="togglePriceCharts"'), "underlying chart collapse button missing");
assert(html.includes('id="priceChartSection"'), "collapsible underlying chart section missing");
assert(/id="togglePriceCharts"[^>]*aria-expanded="false"[^>]*>展开<\/button>/.test(html), "underlying charts should default to collapsed");
assert(/id="priceChartSection"[^>]*hidden/.test(html), "underlying chart panel should be hidden by default");
assert(html.includes('id="chartRange"'), "chart range control missing");
assert(html.includes('id="chartZoomIn"'), "chart zoom control missing");
assert(html.includes('id="inventoryViewChain"'), "inventory option-chain view switch missing");
assert(html.includes('id="inventoryViewMatrix"'), "inventory Strike by DTE view switch missing");
assert(/id="inventoryViewChain"[^>]*aria-pressed="false"/.test(html), "inventory option-chain button should not be active by default");
assert(/id="inventoryViewMatrix"[^>]*class="active"[^>]*aria-pressed="true"/.test(html), "inventory Strike by DTE button should be active by default");
assert(html.includes('let inventoryChainView = "matrix";'), "inventory should initialize in Strike by DTE view");
assert(html.includes('centerScrollableAxis("inventoryChain", ".expiry-sketch", ".chain-map-head.center")'), "inventory option-chain Strike axis should be centered in the viewport");
assert(html.includes('id="candidateViewChain"'), "candidate option-chain view switch missing");
assert(html.includes('id="candidateViewMatrix"'), "candidate Strike by DTE view switch missing");
assert(html.includes('id="whatIfFutureContract"'), "futures What-If selector missing");
assert(html.includes('id="whatIfRun"'), "IB margin What-If action missing");
assert(html.includes('id="whatIfReserveFunds"'), "What-If reserve input missing");
assert(html.includes('id="whatIfCalculateCapacity"'), "verified capacity control missing");
assert(html.includes('id="whatIfResult"'), "What-If result panel missing");
assert(html.includes('id="tradeActivationCode"'), "one-process trading unlock control missing");
assert(html.includes('id="tradeConfirmationPhrase"'), "dynamic order confirmation input missing");
assert(html.includes('id="tradeSubmit"'), "double-confirmed order submission action missing");
assert(html.includes('id="tradeDisarm"'), "immediate trading lock control missing");
assert(html.includes('id="toggleTradePanel"'), "trading panel collapse button missing");
assert(html.includes('id="tradePanel"'), "collapsible trading panel missing");
assert(/id="toggleTradePanel"[^>]*aria-expanded="false"[^>]*>展开<\/button>/.test(html), "trading panel should default to collapsed");
assert(/id="tradePanel"[^>]*hidden/.test(html), "trading panel should be hidden by default");
assert(/\.whatif-panel\[hidden\]\s*\{[^}]*display:\s*none/s.test(html), "trading panel hidden state must override its grid display");
assert(html.includes('const TRADE_GATEWAY_URL = "http://127.0.0.1:8767"'), "loopback-only trade gateway URL missing");
assert(html.includes('限价 LMT（唯一允许）'), "market orders must not be exposed in the first trading release");
assert(!html.includes("placeOrder("), "planner must not expose a live-order call");
assert(html.includes(".inventory-axis-matrix"), "inventory matrix should provide enough scroll space to center the DTE axis");
assert(/\.inventory-matrix-center-head strong\s*\{[^}]*font-size:\s*16px/s.test(html), "DTE axis heading should use a larger font");
assert(/\.inventory-matrix-dte\s*\{[^}]*font-size:\s*14px/s.test(html), "DTE row labels should use a larger font");
assert(html.includes("candidate-combo-panel"), "candidate and combo area should be one panel");
assert(html.includes("sticky-side"), "combo side should stay sticky while chain scrolls");
assert(!html.includes("可覆盖提示"), "node warning side panel should be removed");
assert(html.includes('id="putStrikeMin"'), "put strike selector missing");
assert(html.includes('id="callStrikeMax"'), "call strike selector missing");
assert(html.includes('id="putDeltaChoices"'), "put delta choices missing");
assert(!html.includes('id="chainDteChoices"'), "top-level chain DTE selector should be removed");
assert(html.includes('id="candidateDteChoices"'), "candidate DTE selector missing");
assert(!html.includes('id="expirySketch"'), "standalone sketch should be removed");
assert(html.includes('<option value="all" selected>全部库存</option>'), "inventory DTE should default to all");
assert(html.includes("data/planner/carry_dashboard_chain.csv"), "planner chain should be default source");

const ids = Array.from(html.matchAll(/id="([^"]+)"/g), match => match[1]);
const inputValues = Object.fromEntries(Array.from(html.matchAll(/<(?:input|select)[^>]*id="([^"]+)"[^>]*value="([^"]*)"/g), match => [match[1], match[2]]));
for (const match of html.matchAll(/<select[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {
  const selected = match[2].match(/<option[^>]*value="([^"]+)"[^>]*selected/);
  if (selected) inputValues[match[1]] = selected[1];
}

class StubInput {
  constructor(id, value = "") {
    this.id = id;
    this.value = value;
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.attributes = new Map();
  }

  addEventListener(type, callback) {
    this.listeners[type] = callback;
  }

  dispatch(type = "input") {
    if (this.listeners[type]) this.listeners[type]();
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
}

class StubElement extends StubInput {
  constructor(id) {
    super(id, inputValues[id] || "");
    this.innerHTML = "";
    this.textContent = "";
    this.inputCache = new Map();
  }

  click() {
    if (this.listeners.click) this.listeners.click();
  }

  get options() {
    return Array.from(this.innerHTML.matchAll(/<option[^>]*value="([^"]*)"/g), match => ({ value: match[1] }));
  }

  querySelectorAll(selector) {
    if (selector === "input[type=checkbox]" || selector === "input[type=checkbox]:checked" || selector === "input[type=radio]" || selector === "input:checked") {
      return Array.from(this.innerHTML.matchAll(/<input[^>]*type="(checkbox|radio)"[^>]*value="([^"]+)"([^>]*)>/g), match => {
        if ((selector.endsWith(":checked") || selector === "input:checked") && !/\schecked\b/.test(match[3])) return null;
        if (selector === "input[type=checkbox]" && match[1] !== "checkbox") return null;
        if (selector === "input[type=checkbox]:checked" && match[1] !== "checkbox") return null;
        if (selector === "input[type=radio]" && match[1] !== "radio") return null;
        const id = match[2];
        const input = this.inputCache.get(`${match[1]}:${id}`) || new StubInput("", id);
        input.value = id;
        input.checked = /\schecked\b/.test(match[3]);
        this.inputCache.set(`${match[1]}:${id}`, input);
        return input;
      }).filter(Boolean);
    }
    if (selector !== "input[data-id]") return [];
    return Array.from(this.innerHTML.matchAll(/<input[^>]*data-id="([^"]+)"[^>]*>/g), match => {
      const id = match[1];
      const valueMatch = match[0].match(/\svalue="([^"]*)"/);
      const input = this.inputCache.get(id) || new StubInput("", valueMatch ? valueMatch[1] : "0");
      input.dataset.id = id;
      if (valueMatch) input.value = valueMatch[1];
      this.inputCache.set(id, input);
      return input;
    });
  }
}

const elements = new Map(ids.map(id => [id, new StubElement(id)]));
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new StubElement(id));
    return elements.get(id);
  },
  createElement() {
    return { click() {}, set href(value) {}, set download(value) {} };
  }
};
const RealDate = Date;
class FixedDate extends RealDate {
  constructor(...args) {
    super(...(args.length ? args : ["2026-07-09T12:00:00.000Z"]));
  }

  static now() {
    return new RealDate("2026-07-09T12:00:00.000Z").valueOf();
  }
}

const context = vm.createContext({
  console,
  document,
  Date: FixedDate,
  Math,
  Number,
  String,
  Array,
  Object,
  JSON,
  RegExp,
  Map,
  Set,
  Blob: function Blob() {},
  URL: { createObjectURL: () => "blob:test", revokeObjectURL: () => undefined },
  fetch: async () => ({ ok: false, status: 404, text: async () => "" })
});

new vm.Script(script[1]).runInContext(context);

const refreshContractMonths = JSON.parse(new vm.Script(`JSON.stringify(selectedRefreshContractMonths())`).runInContext(context));
assert.deepStrictEqual(
  refreshContractMonths,
  { ZF: "202609", ZN: "202609", ZC: "202609" },
  "refresh contract selectors should default every product to the September future"
);
assert.strictEqual(new vm.Script(`formatRefreshDuration(4.25)`).runInContext(context), "4.3秒", "short refresh duration formatting is wrong");
assert.strictEqual(new vm.Script(`formatRefreshDuration(65)`).runInContext(context), "1分05秒", "long refresh duration formatting is wrong");
assert.strictEqual(new vm.Script(`firstNonNegativeNum(-100, -1, 0.125)`).runInContext(context), 0.125, "negative IB option-price sentinels must be skipped");
new vm.Script(`lastRefreshDurationSeconds = 12; refreshStartedAtMs = Date.now() - 12000;`).runInContext(context);
assert.strictEqual(new vm.Script(`refreshDurationSeconds({ durationSeconds: 9.5 })`).runInContext(context), 9.5, "backend duration should replace browser polling delay");

new vm.Script(`setRefreshDuration(10, { positionsSeconds: 2, optionsSeconds: 4, futuresSeconds: 3, otherSeconds: 1, totalSeconds: 10 })`).runInContext(context);
assert.strictEqual(elements.get("refreshPositionsDuration").textContent, "2.0秒", "position timing text is wrong");
assert.strictEqual(elements.get("refreshOptionsDuration").textContent, "4.0秒", "option timing text is wrong");
assert.strictEqual(elements.get("refreshFuturesDuration").textContent, "3.0秒", "futures timing text is wrong");
assert.strictEqual(elements.get("refreshOtherDuration").textContent, "1.0秒", "other timing text is wrong");
assert.strictEqual(elements.get("refreshDuration").textContent, "10.0秒", "total timing text is wrong");
assert.strictEqual(elements.get("refreshPositionsBar").style.width, "20%", "position timing segment width is wrong");
assert.strictEqual(elements.get("refreshOptionsBar").style.width, "40%", "option timing segment width is wrong");
const fallbackTimings = JSON.parse(new vm.Script(`JSON.stringify(refreshPhaseTimings({ stdout: [
  "phase timing: positions=4.821",
  "phase timing: futures=7.749",
  "phase timing: total=12.767"
].join("\\n") }))`).runInContext(context));
assert(Math.abs(fallbackTimings.positionsSeconds - 4.821) < 1e-9, "stdout fallback should recover position timing");
assert.strictEqual(fallbackTimings.optionsSeconds, 0, "fast-refresh fallback should not mislabel positions as option-chain time");
assert(Math.abs(fallbackTimings.futuresSeconds - 7.749) < 1e-9, "stdout fallback should recover futures timing");
assert(Math.abs(fallbackTimings.otherSeconds - 0.197) < 1e-9, "stdout fallback should recover unclassified timing");
const detailedTimings = JSON.parse(new vm.Script(`JSON.stringify(refreshPhaseTimings({ stdout: [
  "phase timing: positions=5.000",
  "phase timing: futures.ZF=2.000",
  "phase timing: futures.ZN=3.000",
  "phase timing: futures.ZC=4.000",
  "phase timing: options.ZF=1.000",
  "phase timing: options.ZN=2.000",
  "phase timing: total=20.000"
].join("\\n") }))`).runInContext(context));
assert.strictEqual(detailedTimings.positionsSeconds, 5, "detailed markers should preserve position timing");
assert.strictEqual(detailedTimings.optionsSeconds, 3, "per-product option timings should be summed");
assert.strictEqual(detailedTimings.futuresSeconds, 9, "per-product future timings should be summed");
assert.strictEqual(detailedTimings.otherSeconds, 3, "detailed markers should leave connection/publish time unclassified");
const summarizedLog = new vm.Script(`summarizeRefreshLog([
  "{",
  '  "readiness": {',
  "  }",
  "}",
  "same useful line",
  "same useful line",
  "Error 10090, reqId 1: missing subscription",
  "Error 10090, reqId 2: missing subscription",
  "phase timing: futures.ZF=3.153"
].join("\\n"))`).runInContext(context);
assert(!summarizedLog.includes('"readiness"'), "expanded validation JSON should be removed from the log summary");
assert.strictEqual((summarizedLog.match(/same useful line/g) || []).length, 1, "exact duplicate log lines should be collapsed");
assert(summarizedLog.includes("IB 10090 ×2"), "repeated IB entitlement warnings should be summarized by code");
assert(summarizedLog.includes("耗时 · 期货价格 ZF：3.2秒"), "machine timing markers should have a readable summary");
assert.strictEqual(
  new vm.Script(`rawRefreshLog({ lines: ["tail"], stdout: "full\\ntail" })`).runInContext(context),
  "full\ntail",
  "copyable raw log should prefer the complete stdout over a truncated status tail"
);

const liveMergeResult = JSON.parse(new vm.Script(`JSON.stringify({
  positions: mergeLivePositionRows(
    [{ conId: 1, symbol: "ZN", position: -1, bid: 0.10, ask: 0.12 }, { conId: 2, symbol: "ZF", position: -1 }],
    [{ conId: 1, symbol: "ZN", position: -2, bid: 0.11, ask: null, delta: 0.25 }]
  ),
  futures: mergeLiveFutureRows(
    [{ root: "ZN", month: "202609", price: 109.0 }, { root: "ZN", month: "202612", price: 108.5 }],
    [{ root: "ZN", month: "202609", price: 109.125, marketDataType: 1 }]
  )
})`).runInContext(context));
assert.strictEqual(liveMergeResult.positions.length, 1, "closed positions should disappear from an authoritative live snapshot");
assert.strictEqual(liveMergeResult.positions[0].position, -2, "live position quantity should replace the cached quantity");
assert.strictEqual(liveMergeResult.positions[0].ask, 0.12, "a temporarily empty live field should retain the last usable quote");
assert.strictEqual(liveMergeResult.futures[0].price, 109.125, "streamed selected-future price should take precedence over cached data");
assert.strictEqual(liveMergeResult.futures[1].month, "202612", "unsubscribed cached future months should remain available");
new vm.Script(`updatePortfolioQuoteStatus({
  connected: true, dataMode: "live", sampledAt: "2026-07-16T12:34:56.000Z",
  positionSubscriptions: 38, futureSubscriptions: 3, marketDataTypes: { "1": 41 }
})`).runInContext(context);
assert(elements.get("portfolioQuoteStatus").textContent.includes("行情获取"), "portfolio title should show the concrete acquisition time");
assert(elements.get("portfolioQuoteStatus").textContent.includes("实时行情"), "portfolio title should show the live/delayed data mode");
assert(elements.get("portfolioQuoteStatus").className.includes("live"), "live data mode should use the live status style");
new vm.Script(`updatePortfolioQuoteStatus({
  connected: true, dataMode: "delayed", sampledAt: "2026-07-16T12:34:56.000Z",
  configuredAccount: "OLD", activeAccount: "NEW", accountFallback: true,
  positionSubscriptions: 38, futureSubscriptions: 3, marketDataTypes: { "3": 41 }
})`).runInContext(context);
assert(elements.get("portfolioQuoteStatus").textContent.includes("约15–20分钟"), "delayed mode should disclose the documented delay range");
assert(elements.get("portfolioQuoteStatus").title.includes("本机获取时间"), "delayed timestamp must be described as acquisition time");
assert(elements.get("portfolioQuoteStatus").title.includes("已自动切换"), "account fallback should be visible in the status tooltip");

new vm.Script(`
  positionRows = samplePositions();
  chainRows = sampleChain();
  futurePriceRows = sampleFuturePrices();
  barsRows = sampleBars();
  accountRows = [];
  proposed = {};
  choiceUserTouched = {};
  render();
`).runInContext(context);

assert.strictEqual(elements.get("priceChartSection").hidden, true, "underlying charts should start collapsed");
assert.strictEqual(elements.get("togglePriceCharts").textContent, "展开", "collapsed chart button should offer to expand");
assert.strictEqual(elements.get("togglePriceCharts").getAttribute("aria-expanded"), "false", "default chart accessibility state is wrong");
elements.get("togglePriceCharts").click();
assert.strictEqual(elements.get("priceChartSection").hidden, false, "chart expand button should restore the underlying chart panel");
assert.strictEqual(elements.get("togglePriceCharts").textContent, "折叠", "expanded chart button should offer to collapse");
assert.strictEqual(elements.get("togglePriceCharts").getAttribute("aria-expanded"), "true", "expanded chart button accessibility state is wrong");
elements.get("togglePriceCharts").click();
assert.strictEqual(elements.get("priceChartSection").hidden, true, "chart collapse button should hide the underlying chart panel");

assert.strictEqual(elements.get("tradePanel").hidden, true, "trading panel should start collapsed");
assert.strictEqual(elements.get("toggleTradePanel").textContent, "展开", "collapsed trading button should offer to expand");
assert.strictEqual(elements.get("toggleTradePanel").getAttribute("aria-expanded"), "false", "default trading accessibility state is wrong");
elements.get("toggleTradePanel").click();
assert.strictEqual(elements.get("tradePanel").hidden, false, "trading expand button should reveal the trading panel");
assert.strictEqual(elements.get("toggleTradePanel").textContent, "折叠", "expanded trading button should offer to collapse");
assert.strictEqual(elements.get("toggleTradePanel").getAttribute("aria-expanded"), "true", "expanded trading accessibility state is wrong");
elements.get("toggleTradePanel").click();
assert.strictEqual(elements.get("tradePanel").hidden, true, "trading collapse button should hide the trading panel");

assert.strictEqual(elements.get("inventoryViewMatrix").getAttribute("aria-pressed"), "true", "inventory should default to the Strike by DTE view");
assert(elements.get("inventoryChain").innerHTML.includes("inventory-matrix"), "default inventory view should render a Strike by DTE matrix");
assert(elements.get("inventoryChain").innerHTML.includes("DTE ↓"), "inventory matrix should place DTE on the vertical axis");
assert(elements.get("inventoryChain").innerHTML.includes("高 Strike ← · → 低 Strike"), "inventory matrix should place strikes around the current-price axis");
assert(elements.get("inventoryChain").innerHTML.includes("inventory-matrix-center-head"), "inventory matrix should render DTE as the center column");
assert(elements.get("inventoryChain").innerHTML.includes("inventory-axis-matrix"), "inventory matrix should make the DTE axis centerable in the viewport");
assert(elements.get("inventoryChain").innerHTML.includes("matrix-side-badge"), "inventory matrix should distinguish Call and Put positions inside cells");
assert(elements.get("inventoryChain").innerHTML.includes("matrix-position-pnl"), "inventory matrix should include unrealized PnL");
assert(elements.get("inventoryChain").innerHTML.includes("Bid / Ask"), "inventory matrix should label bid and ask quotes");
assert(elements.get("inventoryChainNote").textContent.includes("ITM Call/Put 会跨轴归位"), "inventory matrix note should explain ITM cross-axis placement");
assert.strictEqual(elements.get("inventoryViewMatrix").getAttribute("aria-pressed"), "true", "inventory matrix accessibility state is wrong");
elements.get("inventoryViewChain").click();
assert(elements.get("inventoryChain").innerHTML.includes("chain-map"), "inventory option-chain switch should restore the original view");
assert.strictEqual(elements.get("inventoryViewChain").getAttribute("aria-pressed"), "true", "inventory option-chain accessibility state is wrong after switching views");

const centeredMatrixResult = new vm.Script(`
  (() => {
    const cfg = { ...config(), underlying: "ZF", allowed: ["ZF"] };
    const rows = [
      { underlying: "ZF", expiry: "20260710", dte: 1, strike: 107.25, right: "C", position: -1, contracts: 1, marketValue: -24, unrealizedPnL: 6, delta: 0.18, bid: 0.02, ask: 0.025, underlyingPrice: 106.75 },
      { underlying: "ZF", expiry: "20260710", dte: 1, strike: 106.50, right: "P", position: -2, contracts: 2, marketValue: -31, unrealizedPnL: -4, delta: -0.22, bid: 0.03, ask: 0.035, underlyingPrice: 106.75 }
    ];
    const html = inventoryStrikeDteHtml(rows, cfg, { positions: rows, futures: [], markHighDelta: true, highDeltaThreshold: 0.40 });
    return JSON.stringify({
      html,
      callIndex: html.indexOf('matrix-header-side">Call'),
      dteIndex: html.indexOf("inventory-matrix-center-head"),
      putIndex: html.indexOf('matrix-header-side">Put')
    });
  })()
`).runInContext(context);
const centeredMatrix = JSON.parse(centeredMatrixResult);
assert(centeredMatrix.callIndex >= 0, "centered inventory matrix should render Call strikes");
assert(centeredMatrix.putIndex >= 0, "centered inventory matrix should render Put strikes");
assert(centeredMatrix.callIndex < centeredMatrix.dteIndex && centeredMatrix.dteIndex < centeredMatrix.putIndex, "centered inventory matrix order must be Call, DTE, Put");
assert(centeredMatrix.html.includes("PnL +$6"), "centered inventory matrix should show positive unrealized PnL");
assert(centeredMatrix.html.includes("PnL $-4"), "centered inventory matrix should show negative unrealized PnL");

const itmAxisResult = new vm.Script(`
  (() => {
    const axis = matrixAxisEntries([
      { right: "C", strike: 109.50 },
      { right: "C", strike: 109.25 },
      { right: "P", strike: 109.50 },
      { right: "P", strike: 109.00 }
    ], 109.266);
    const markedCard = inventoryMatrixPositionHtml({
      underlying: "ZN", right: "C", strike: 109.25, position: -2, contracts: 2,
      marketValue: -234, unrealizedPnL: -144, delta: 0.50, bid: 0.10938, ask: 0.125
    }, 109.25, 109.266, { markHighDelta: true, highDeltaThreshold: 0.40 });
    return JSON.stringify({ axis, markedCard });
  })()
`).runInContext(context);
const itmAxis = JSON.parse(itmAxisResult);
assert(itmAxis.axis.right.some(entry => entry.right === "C" && Math.abs(entry.strike - 109.25) < 1e-9 && entry.itm), "ITM Call should move to the low-strike side of the DTE axis");
assert(itmAxis.axis.left.some(entry => entry.right === "P" && Math.abs(entry.strike - 109.50) < 1e-9 && entry.itm), "ITM Put should move to the high-strike side of the DTE axis");
assert(itmAxis.markedCard.includes(">ITM<") && itmAxis.markedCard.includes(">高Δ<"), "ITM and high-Delta badges should be shown together");

const deepItmExposureResult = new vm.Script(`
  (() => {
    const cfg = { ...config(), underlying: "ZN", allowed: ["ZN"], manualFuturePrice: 109.3125 };
    const rows = [
      { symbol: "ZN", secType: "FUT", position: -1, localSymbol: "ZNU6", delta: 1, underlyingPrice: 109.3125 },
      { symbol: "ZN", secType: "FOP", position: 1, localSymbol: "HY3N6 C1087", expiry: "20260710", strike: 108.75, right: "C", mid: 0.5625, underlyingPrice: 109.3125, delta: 0.8991115407769299 },
      { symbol: "ZN", secType: "FOP", position: 1, localSymbol: "ZN3N6 C1095", expiry: "20260710", strike: 109.5, right: "C", mid: 0.1, underlyingPrice: 109.3125, delta: 0.30 },
      { symbol: "ZN", secType: "FOP", position: -1, localSymbol: "HY3N6 P1100", expiry: "20260710", strike: 110, right: "P", mid: 0.8, underlyingPrice: 109.3125, delta: -0.20 }
    ];
    const futures = parseFuturePositions(rows, cfg);
    const exposure = portfolioDeltaExposure(rows, cfg, futures);
    return JSON.stringify(exposure);
  })()
`).runInContext(context);
const deepItmExposure = JSON.parse(deepItmExposureResult);
assert.strictEqual(deepItmExposure.deepItmOptions.length, 1, "only the zero-time-value, near-unit-delta call should be a future equivalent");
assert.strictEqual(deepItmExposure.deepItmOptions[0].localSymbol, "HY3N6 C1087", "the intended deep-ITM call was not identified");
assert(Math.abs(deepItmExposure.deepItmOptions[0].timeValue) < 1e-9, "deep-ITM call time value should be zero");
assert(Math.abs(deepItmExposure.deepItmFutureEquivalent - 0.8991115407769299) < 1e-9, "deep-ITM future-equivalent exposure is wrong");
assert(Math.abs(deepItmExposure.optionDelta - 0.20) < 1e-9, "option delta should include short options only");
assert.strictEqual(deepItmExposure.futureDelta, -1, "actual futures delta is wrong");
assert(Math.abs(deepItmExposure.equivalentFutureDelta + 0.1008884592230701) < 1e-9, "equivalent futures should combine actual futures with deep-ITM long options only");
assert(Math.abs(deepItmExposure.portfolioDelta - 0.0991115407769299) < 1e-9, "portfolio delta must equal short-option delta plus equivalent futures, excluding ordinary long options");

const currentZnEquivalentResult = new vm.Script(`
  (() => {
    const cfg = { ...config(), underlying: "ZN", allowed: ["ZN"], manualFuturePrice: 109.078125 };
    const rows = [{
      symbol: "ZN", secType: "FOP", position: 1, localSymbol: "HY3N6 C1087",
      expiry: "20260710", strike: 108.75, right: "C", mid: 0.359375,
      underlyingPrice: 109.078125, delta: 0.9268225743656249
    }];
    const accepted = portfolioDeltaExposure(rows, cfg, []);
    const rejected = portfolioDeltaExposure(rows, { ...cfg, equivalentFutureMaxTimeValueTicks: 1 }, []);
    return JSON.stringify({ accepted, rejected });
  })()
`).runInContext(context);
const currentZnEquivalent = JSON.parse(currentZnEquivalentResult);
assert.strictEqual(currentZnEquivalent.accepted.deepItmOptions.length, 1, "the live-like ZN call should pass the default two-tick time-value limit");
assert(Math.abs(currentZnEquivalent.accepted.deepItmOptions[0].timeValueTicks - 2) < 1e-9, "the live-like ZN call should have two ticks of time value");
assert(Math.abs(currentZnEquivalent.accepted.equivalentFutureDelta - 0.9268225743656249) < 1e-9, "the live-like ZN call equivalent future is wrong");
assert.strictEqual(currentZnEquivalent.rejected.deepItmOptions.length, 0, "a one-tick limit should reject the same ZN call");
assert(currentZnEquivalent.rejected.rejectedDeepItmOptions[0].deepItmReasons.some(reason => reason.includes("时间价值 2.0ticks > 1.0ticks")), "rejected equivalent-future candidates should explain the failed parameter");

const normalizedZcPremiumResult = new vm.Script(`
  (() => {
    const cfg = { ...config(), underlying: "ZC", allowed: ["ZC"] };
    const rows = [{
      symbol: "ZC", secType: "FOP", position: -1, expiry: "20260717", dte: 1,
      strike: 4.25, right: "P", price: 0.625, marketValue: -3125,
      valueSource: "portfolio", contractMultiplier: 5000, multiplier: 50, costBasis: -34.48
    }];
    return JSON.stringify(parseShortPositions(rows, cfg, { respectInventoryDte: false })[0]);
  })()
`).runInContext(context);
const normalizedZcPremium = JSON.parse(normalizedZcPremiumResult);
assert.strictEqual(normalizedZcPremium.remainingPremium, 31.25, "ZC raw-contract-multiplier portfolio value should be normalized to $50 per quoted cent");
assert(Math.abs(normalizedZcPremium.unrealizedPnL - 3.23) < 1e-9, "ZC normalized unrealized PnL is wrong");

const cumulativePremiumRows = JSON.parse(new vm.Script(`JSON.stringify(premiumCumulativeRows([
  { expiry: "2026-07-24", dte: 4, remainingPremium: 250, weightedPremium: 200 },
  { expiry: "2026-07-20", dte: 0, remainingPremium: 100, weightedPremium: 90 },
  { expiry: "2026-07-21", dte: 1, remainingPremium: 200, weightedPremium: 180 }
]))`).runInContext(context));
assert.deepStrictEqual(cumulativePremiumRows.map(row => row.dte), [0, 1, 4], "premium curve should sort expiries by DTE");
assert.deepStrictEqual(cumulativePremiumRows.map(row => row.cumulativePremium), [100, 300, 550], "raw premium curve should accumulate by expiry");
assert.deepStrictEqual(cumulativePremiumRows.map(row => row.cumulativeWeightedPremium), [90, 270, 470], "weighted premium curve should accumulate by expiry");
const premiumTooltipHtml = new vm.Script(`premiumCurveTooltipHtml(premiumCumulativeRows([
  { expiry: "2026-07-20", dte: 0, products: "ZF / ZN", remainingPremium: 100, weightedPremium: 90 },
  { expiry: "2026-07-24", dte: 4, products: "ZC", remainingPremium: 250, weightedPremium: 200 }
])[1])`).runInContext(context);
assert(premiumTooltipHtml.includes("该到期日"), "premium tooltip should label per-expiry values");
assert(premiumTooltipHtml.includes("累计"), "premium tooltip should label cumulative values");
assert(premiumTooltipHtml.includes("$250") && premiumTooltipHtml.includes("$350"), "premium tooltip should show raw per-expiry and cumulative values");
assert(premiumTooltipHtml.includes("$200") && premiumTooltipHtml.includes("$290"), "premium tooltip should show weighted per-expiry and cumulative values");

assert(elements.get("targetSummary").innerHTML.includes("本月目标"), "target summary missing");
assert(elements.get("targetSummary").innerHTML.includes("本月已完成"), "completed PnL summary missing");
assert(elements.get("inventoryBars").innerHTML.includes("按方向"), "inventory distribution missing");
assert(!elements.get("inventoryBars").innerHTML.includes("按日期维度"), "duplicated date dimension should be removed");
assert(elements.get("inventoryBars").innerHTML.includes("1DTE"), "expiry axis should include current inventory DTEs");
assert(!elements.get("inventoryBars").innerHTML.includes("7DTE"), "expiry axis should not show empty chain DTEs");
assert(!elements.get("inventoryBars").innerHTML.includes("12DTE"), "expiry axis should not show farther empty chain DTEs");
assert(elements.get("greekSummary").innerHTML.includes("合约张数"), "greek summary missing");
assert(elements.get("greekSummary").innerHTML.includes("期货张数"), "future quantity summary missing");
assert(elements.get("futurePricePrompt").innerHTML.includes("手动期货价"), "manual future price input missing");
assert(elements.get("productOverview").innerHTML.includes("ZF"), "product overview should include ZF");
assert(elements.get("productOverview").innerHTML.includes("ZN"), "product overview should include ZN");
assert(elements.get("productOverview").innerHTML.includes("等效期货"), "product overview should show actual futures plus deep-ITM equivalents");
assert(elements.get("productOverview").innerHTML.includes("期权Delta"), "product overview delta should be explicitly option-only");
assert(elements.get("productOverview").innerHTML.includes("组合Delta"), "product overview should include futures-aware portfolio delta");
assert(!elements.get("productOverview").innerHTML.includes("保证金"), "product overview should no longer show margin");
assert(elements.get("productOverview").innerHTML.includes("期权金指标"), "product overview should include delta-weighted premium");
assert(elements.get("productOverview").innerHTML.includes("product-icon"), "product overview should include recognizable product icons");
assert(elements.get("productOverview").innerHTML.includes("当前查看"), "product overview should mark the active product prominently");
assert(elements.get("productOverview").innerHTML.includes("product-signal-row"), "product overview should include position and data status signals");
assert(elements.get("productOverview").innerHTML.includes("product-metric-label"), "product metrics should include visual metric markers");
assert(elements.get("portfolioPremiumSummary").innerHTML.includes("全部剩余权利金"), "overall premium total missing");
assert(elements.get("portfolioPremiumSummary").innerHTML.includes("Delta 加权期权金"), "overall delta-weighted premium missing");
assert(elements.get("premiumExpiryTable").innerHTML.includes("到期日"), "premium expiry overview missing expiry column");
assert(elements.get("premiumExpiryTable").innerHTML.includes("剩余权利金"), "premium expiry overview missing raw premium");
assert(elements.get("premiumExpiryTable").innerHTML.includes("Delta 加权期权金"), "premium expiry overview missing weighted premium");
assert(elements.get("premiumCurveNote").textContent.includes("个到期日"), "premium curve note should describe its expiry coverage");
assert(elements.get("premiumCurveNote").textContent.includes("风险折减"), "premium curve note should explain the shaded gap");
assert(elements.get("premiumCurveRawTotal").textContent.startsWith("$"), "raw premium curve total should render as money");
assert(elements.get("premiumCurveWeightedTotal").textContent.startsWith("$"), "weighted premium curve total should render as money");
assert(elements.get("priceChartNote").textContent.includes("ZF"), "price chart should follow the selected product");
assert(elements.get("priceChartNote").textContent.includes("范围"), "price chart note should include interactive range state");
assert(elements.get("priceChartNote").textContent.includes("日线"), "price chart note should include daily chart coverage");
assert(elements.get("priceChartNote").textContent.includes("30分钟"), "price chart note should include 30 minute chart coverage");
assert(elements.get("greekDteTable").innerHTML.includes("DTE分组"), "DTE greek table missing");
assert(!elements.get("greekDteTable").innerHTML.includes("6.000"), "contracts should render as integers");
assert(elements.get("inventoryChain").innerHTML.includes("Strike"), "inventory chain strike column missing");
assert(elements.get("candidateTable").innerHTML.includes("data-id="), "candidate quantity inputs missing");
assert(elements.get("candidateTable").innerHTML.includes("Call"), "candidate chain call side missing");
assert(elements.get("candidateTable").innerHTML.includes("Put"), "candidate chain put side missing");
assert(elements.get("candidateTable").innerHTML.includes("当前期货价"), "future price marker missing");
assert(elements.get("candidateTable").innerHTML.includes("Bid/Ask"), "candidate full quote header missing");
assert(elements.get("candidateTable").innerHTML.includes("2026-07-10"), "candidate chain should show readable expiry date above strikes");
assert.strictEqual(elements.get("candidateViewMatrix").getAttribute("aria-pressed"), "true", "candidate chain should default to the Strike by DTE view");
assert(elements.get("candidateTable").innerHTML.includes("candidate-matrix"), "candidate Strike by DTE view should render a centered matrix");
assert(elements.get("candidateTable").innerHTML.includes("高 Strike ← · → 低 Strike"), "candidate matrix should use the same current-price-centered strike axis");
assert(elements.get("candidateTable").innerHTML.includes("保证金"), "candidate matrix should retain margin information");
assert(elements.get("candidateTable").innerHTML.includes("评分"), "candidate matrix should retain score information");
assert(elements.get("candidateTable").innerHTML.includes("选择预检"), "candidate cards should populate the margin preflight draft");
assert.strictEqual(elements.get("whatIfRun").disabled, true, "order preview must remain disabled while the trade gateway is offline");
assert(elements.get("whatIfResult").innerHTML.includes("独立交易服务未启动"), "offline trading safety status should be visible");
elements.get("candidateViewChain").click();
assert(elements.get("candidateTable").innerHTML.includes("chain-map"), "candidate option-chain switch should restore the original view");
assert.strictEqual(elements.get("candidateViewChain").getAttribute("aria-pressed"), "true", "candidate option-chain accessibility state is wrong");
elements.get("candidateViewMatrix").click();
assert(elements.get("candidateTable").innerHTML.includes("candidate-matrix"), "candidate matrix switch should restore the centered view");
assert(elements.get("beforeAfter").innerHTML.includes("当前"), "before/after table missing current row");
assert(elements.get("nodeTable").innerHTML.includes("106.500"), "short position strike missing");
assert(!elements.get("nodeTable").innerHTML.includes("ZF-P-105.500"), "long option leaked into core node table");

const whatIfUiResult = new vm.Script(`
  (() => {
    const savedCandidates = candidates;
    candidates = [{
      id: "12345", conId: 12345, secType: "FOP", exchange: "CBOT", underlying: "ZF",
      localSymbol: "ZF1N6 P1065", expiry: "20260710", strike: 106.5, right: "P",
      bid: 0.03125, ask: 0.046875, mid: 0.0390625
    }];
    selectWhatIfCandidate("12345");
    const selectedHtml = document.getElementById("whatIfSelected").innerHTML;
    const action = document.getElementById("whatIfAction").value;
    const limitPrice = Number(document.getElementById("whatIfLimitPrice").value);
    const runDisabled = document.getElementById("whatIfRun").disabled;
    whatIfResponse = {
      supported: true, reserve_funds: 100, binding_constraint: "available_funds",
      available_headroom_after: 900, excess_headroom_after: 1200, max_quantity: 7,
      max_quantity_is_search_cap: false, probe_count: 6,
      first_unsupported_result: { quantity: 8 },
      requested: {
        contract_label: "ZF1N6 P1065", action: "SELL", quantity: 1, requested_at: "2026-07-16 20:00:00 +0800",
        initial_margin_before: 1000, initial_margin_change: 250, initial_margin_after: 1250,
        maintenance_margin_before: 800, maintenance_margin_change: 200, maintenance_margin_after: 1000,
        available_funds_before: 1250, estimated_available_funds_after: 1000,
        excess_liquidity_before: 1500, estimated_excess_liquidity_after: 1300,
        warning_text: ""
      }
    };
    renderWhatIfResult();
    const resultHtml = document.getElementById("whatIfResult").innerHTML;
    candidates = savedCandidates;
    whatIfInstrument = null;
    whatIfResponse = null;
    return JSON.stringify({ selectedHtml, action, limitPrice, runDisabled, resultHtml });
  })()
`).runInContext(context);
const whatIfUi = JSON.parse(whatIfUiResult);
assert(whatIfUi.selectedHtml.includes("ZF1N6 P1065") && whatIfUi.selectedHtml.includes("conId 12345"), "candidate selection should populate the What-If ticket");
assert.strictEqual(whatIfUi.action, "SELL", "candidate option What-If should default to the sell direction");
assert(Math.abs(whatIfUi.limitPrice - 0.03125) < 1e-9, "candidate option What-If should default to bid limit");
assert.strictEqual(whatIfUi.runDisabled, true, "selecting a candidate must not enable the IB order protocol");
assert(whatIfUi.resultHtml.includes("当前数量通过保证金预检"), "passing What-If result should be explicit");
assert(whatIfUi.resultHtml.includes("7 张") && whatIfUi.resultHtml.includes("8 张不通过"), "verified capacity boundary should be visible");

const tradeConfirmationUi = JSON.parse(new vm.Script(`
  (() => {
    tradeSessionToken = "session-token";
    tradeGatewayStatus = {
      ...tradeGatewayStatus, online: true, mode: "paper", armed: true,
      maxOrderQuantity: 5, maxPreviewQuantity: 50, minimumReserveFunds: 1000
    };
    whatIfInstrument = { conId: 12345, secType: "FOP", exchange: "CBOT", underlying: "ZF", label: "ZF1N6 P1065" };
    tradePreview = {
      previewId: "preview-1", fingerprint: "abcdef1234567890", fingerprintShort: "abcdef123456",
      expiresAt: Date.now() / 1000 + 30, confirmationPhrase: "确认 PAPER SELL 1 ZF1N6 P1065 @0.03125",
      submittable: true
    };
    renderWhatIfPanel(config());
    const previewDisabled = document.getElementById("whatIfRun").disabled;
    document.getElementById("tradeConfirmationPhrase").value = tradePreview.confirmationPhrase;
    renderTradeConfirmation();
    const submitDisabled = document.getElementById("tradeSubmit").disabled;
    const fingerprint = document.getElementById("tradeFingerprint").textContent;
    tradeSessionToken = "";
    tradeGatewayStatus = { ...tradeGatewayStatus, online: false, mode: "offline", armed: false };
    tradePreview = null;
    whatIfInstrument = null;
    return JSON.stringify({ previewDisabled, submitDisabled, fingerprint });
  })()
`).runInContext(context));
assert.strictEqual(tradeConfirmationUi.previewDisabled, false, "armed paper session should enable margin preview");
assert.strictEqual(tradeConfirmationUi.submitDisabled, false, "exact dynamic confirmation phrase should enable final submit");
assert(tradeConfirmationUi.fingerprint.includes("abcdef123456") && tradeConfirmationUi.fingerprint.includes("preview-1"), "order fingerprint and preview id must be visible");

const qtyInput = elements.get("candidateTable").querySelectorAll("input[data-id]")[0];
assert(qtyInput, "quantity input listener missing");
qtyInput.value = "2";
qtyInput.dispatch("input");

assert(elements.get("beforeAfter").innerHTML.includes("新增"), "manual allocation did not update before/after");
assert(elements.get("selectedOptions").innerHTML.includes("目标 2张"), "manual allocation did not update selected list");
assert(elements.get("afterDteTable").innerHTML.includes("DTE分组"), "manual allocation did not update DTE table");

const positionsCsv = fs.readFileSync("data/planner/carry_dashboard_positions.csv", "utf8");
const chainCsv = fs.readFileSync("data/planner/carry_dashboard_chain.csv", "utf8");
const liveResult = new vm.Script(`
  positionRows = parseInput(${JSON.stringify(positionsCsv)});
  chainRows = parseInput(${JSON.stringify(chainCsv)});
  futurePriceRows = [];
  barsRows = [];
  proposed = {};
  choiceUserTouched = {};
  document.getElementById("activeUnderlying").value = "ZF";
  render();
  const pressure = targetPressure(shortPositions, config());
  JSON.stringify({
    shortByDte: shortPositions.reduce((acc, row) => {
      const key = Math.max(0, Math.round(row.dte));
      acc[key] = (acc[key] || 0) + row.contracts;
      return acc;
    }, {}),
    monthlyTarget: pressure.monthlyTargetProfit,
    currentPremium: pressure.currentShortRemainingPremium,
    remainingTarget: pressure.remainingTarget,
    putStrikeMin: config().putStrikeMin,
    callStrikeMax: config().callStrikeMax,
    putStrikeOptions: Array.from(document.getElementById("putStrikeMin").options).map(option => Number(option.value)).filter(Number.isFinite),
    callStrikeOptions: Array.from(document.getElementById("callStrikeMax").options).map(option => Number(option.value)).filter(Number.isFinite),
    selectedCandidateDtes: config().selectedCandidateDtes,
    candidateDteHtml: document.getElementById("candidateDteChoices").innerHTML,
    futurePrompt: document.getElementById("futurePricePrompt").innerHTML,
    putDeltaHtml: document.getElementById("putDeltaChoices").innerHTML,
    creditSource: document.getElementById("creditSource").value,
    inventoryHtml: document.getElementById("inventoryChain").innerHTML,
    candidateHtml: document.getElementById("candidateTable").innerHTML,
    candidateCount: candidates.length,
    candidateLimit: config().candidateLimit,
    emptyCandidateCells: (document.getElementById("candidateTable").innerHTML.match(/candidate-matrix-cell[^\"]* empty/g) || []).length,
    nodeHtml: document.getElementById("nodeTable").innerHTML,
    candidateDisplayDtes: unifiedDtes(candidates, config().selectedCandidateDtes),
    candidateCallDeltas: candidates
      .filter(row => row.right === "C")
      .map(row => Math.abs(row.delta)),
    candidateStrikeWindowOk: (() => {
      const cfg = config();
      const spot = referencePriceFor("ZF", chainRows, shortPositions, cfg, futurePositions).price;
      return candidates
        .filter(row => !row.isCurrentOnly)
        .every(row => row.right === "C"
          ? row.strike > spot && row.strike <= cfg.callStrikeMax + EPS
          : row.strike < spot && row.strike >= cfg.putStrikeMin - EPS);
    })()
  });
`).runInContext(context);
const live = JSON.parse(liveResult);
const liveDtes = Object.keys(live.shortByDte).map(Number).filter(Number.isFinite);
const farthestLiveDte = Math.max(...liveDtes);
assert(liveDtes.length > 0, "live inventory should include current short positions");
assert(farthestLiveDte > 2, "live inventory fixture should include holdings beyond the near planning window");
assert.strictEqual(live.remainingTarget, Math.max(live.monthlyTarget - live.currentPremium, 0), "target pressure should deduct current remaining premium");
assert(live.remainingTarget < live.monthlyTarget, "remaining target should be lower than gross monthly target when premium exists");
assert(live.putStrikeOptions.includes(live.putStrikeMin), "put strike selector should default to a real chain strike");
assert(live.callStrikeOptions.includes(live.callStrikeMax), "call strike selector should default to a real chain strike");
assert(live.putStrikeMin < 106.914, "put strike selector should default below the ZF spot");
assert(live.callStrikeMax > 106.914, "call strike selector should default above the ZF spot");
assert(live.candidateDteHtml.includes('type="checkbox"'), "candidate DTE selector should render checkboxes");
assert(live.selectedCandidateDtes.length > 0, "candidate DTE selector should choose default DTEs");
assert(live.selectedCandidateDtes.some(dte => dte > 10), "candidate DTE selector should include farther chain expiries by default");
assert.deepStrictEqual(live.candidateDisplayDtes, live.selectedCandidateDtes, "candidate matrix should retain every selected DTE column, including empty ones");
assert(live.candidateCallDeltas.every(delta => delta > 0 && delta <= 0.20 + 1e-9), "candidate call cells must honor the selected delta ceiling");
assert.strictEqual(live.creditSource, "bid", "sell-side credit source should default to bid");
assert(/chain|positions|future-position/.test(live.futurePrompt), "future price should come from planner data");
assert(/10\d\.\d+/.test(live.futurePrompt), "future price prompt should show a numeric ZF price");
assert(live.putDeltaHtml.includes('type="radio"'), "delta limit should render as a single-choice radio group");
assert(!live.putDeltaHtml.includes('type="checkbox"'), "delta limit should not render multiple checkboxes");
assert(live.putDeltaHtml.includes('value="0.5"'), "delta limit should allow 0.50");
assert(/\d+\.\d+\s\/\s\d+\.\d+/.test(live.inventoryHtml), "inventory chain should show bid/ask quotes from the option chain");
assert(/-\d+ 张 zf \d/.test(live.inventoryHtml), "inventory chain should use compact signed position names");
assert(live.inventoryHtml.includes("chain-map"), "inventory chain should render as a horizontal option-chain map");
assert(live.candidateHtml.includes("candidate-matrix"), "candidate chain should render as a centered Strike by DTE matrix");
assert(live.candidateHtml.includes("candidate-sparse-matrix"), "candidate matrix should render only sparse eligible rows");
assert(live.candidateCount <= live.candidateLimit, "candidate display count must honor the configured limit");
assert.strictEqual(live.emptyCandidateCells, 0, "candidate matrix should not generate blank Strike by DTE Cartesian cells");
assert(live.nodeHtml.includes("chain-map"), "adjusted node view should render as a horizontal option-chain map");
assert(live.inventoryHtml.includes("DTE Call") && live.inventoryHtml.includes("DTE Put"), "inventory map should separate call and put sides by DTE");
assert(live.candidateHtml.includes("高 Strike ← · → 低 Strike"), "candidate matrix should place high and low strikes around the DTE axis");
assert(live.candidateHtml.includes("当前期货价"), "candidate DTE axis should show the current futures price");
assert(live.inventoryHtml.includes("title-value"), "inventory map should place market value beside the option name");
assert(live.candidateHtml.includes("title-income"), "candidate map should show actual premium income next to the option name");
assert(/title-income">\+\$/.test(live.candidateHtml), "candidate map income should be placed immediately after the option name");
assert(live.inventoryHtml.includes("invalid-zone"), "inventory map should whiten empty cells on the wrong side of spot");
assert(!live.candidateHtml.includes("Bid/Ask - /"), "candidate map should exclude rows without a sellable bid");
assert(live.candidateStrikeWindowOk, "openable candidate rows should respect the strike-distance window");
assert(!live.inventoryHtml.includes("strike-meta"), "inventory strike cells should not repeat DTE");
assert(!live.candidateHtml.includes("strike-meta"), "candidate strike cells should not repeat DTE");

const candidateLimitResult = JSON.parse(new vm.Script(`
  (() => {
    const rows = Array.from({ length: 25 }, (_, index) => ({
      underlying: "ZF", bid: 0.1, finalScore: 100 - index, dte: index % 3,
      right: index % 2 ? "P" : "C", strike: 100 + index
    }));
    const limited = buildCandidateDisplayRows(rows, { ...config(), underlying: "ZF", candidateLimit: 10 });
    const sparse = candidateStrikeDteHtml([{
      id: "only-call", underlying: "ZF", right: "C", strike: 108, dte: 0, expiry: "20260709",
      bid: 0.1, ask: 0.2, estimatedCredit: 0.1, delta: 0.1, multiplier: 1000,
      marginEstimate: 100, finalScore: 1, currentContracts: 0, underlyingPrice: 107
    }], { ...config(), underlying: "ZF", selectedCandidateDtes: [0, 1] }, { displayDtes: [0, 1], dteRows: [] });
    return JSON.stringify({ count: limited.length, bestScore: Math.max(...limited.map(row => row.finalScore)), sparse });
  })()
`).runInContext(context));
assert.strictEqual(candidateLimitResult.count, 10, "candidate limit should cap the ranked display rows");
assert.strictEqual(candidateLimitResult.bestScore, 100, "candidate limit should retain the highest-ranked rows");
assert(candidateLimitResult.sparse.includes("该 DTE 当前未显示 Put：没有合格候选，或综合评分未进入显示数量范围"), "an empty candidate side should explain why no data is shown");
assert(candidateLimitResult.sparse.includes("该 DTE 当前未显示 Call：没有合格候选，或综合评分未进入显示数量范围"), "an entirely empty requested DTE should explain the missing candidates");

const liveSignatureResult = new vm.Script(`
  (() => {
    const base = { connected: true, sampledAt: "one", positions: [{ conId: 1, marketPrice: 2 }], futures: [] };
    return JSON.stringify({
      sameData: livePositionSnapshotSignature(base) === livePositionSnapshotSignature({ ...base, sampledAt: "two" }),
      changedPrice: livePositionSnapshotSignature(base) !== livePositionSnapshotSignature({ ...base, positions: [{ conId: 1, marketPrice: 3 }] })
    });
  })()
`).runInContext(context);
const liveSignature = JSON.parse(liveSignatureResult);
assert.strictEqual(liveSignature.sameData, true, "live polling should not redraw when only the sample timestamp changes");
assert.strictEqual(liveSignature.changedPrice, true, "live polling must redraw when a quote changes");

const globexDteResult = JSON.parse(new vm.Script(`
  (() => {
    const beijingMorning = new Date("2026-07-21T02:31:23.000Z");
    const betweenTreasuryAndCornOpen = new Date("2026-07-20T23:30:00.000Z");
    return JSON.stringify({
      zfAtBeijingMorning: dteFromExpiry("20260721", "ZF", beijingMorning),
      znAtBeijingMorning: dteFromExpiry("20260721", "ZN", beijingMorning),
      zcAtBeijingMorning: dteFromExpiry("20260721", "ZC", beijingMorning),
      zfAfterTreasuryOpen: dteFromExpiry("20260721", "ZF", betweenTreasuryAndCornOpen),
      zcBeforeCornOpen: dteFromExpiry("20260721", "ZC", betweenTreasuryAndCornOpen)
    });
  })()
`).runInContext(context));
assert.strictEqual(globexDteResult.zfAtBeijingMorning, 0, "ZF expiry should be 0DTE after the prior-evening Globex session opens");
assert.strictEqual(globexDteResult.znAtBeijingMorning, 0, "ZN expiry should be 0DTE after the prior-evening Globex session opens");
assert.strictEqual(globexDteResult.zcAtBeijingMorning, 0, "ZC expiry should be 0DTE after the corn night session opens");
assert.strictEqual(globexDteResult.zfAfterTreasuryOpen, 0, "Treasury trade date should roll at 17:00 CT");
assert.strictEqual(globexDteResult.zcBeforeCornOpen, 1, "Corn trade date should remain on the civil date before 19:00 CT");

const dteRecalculationResult = new vm.Script(`
  const savedChainRows = chainRows;
  chainRows = [
    { symbol: "ZF", expiry: "20260708", dte: "0" },
    { symbol: "ZF", expiry: "20260710", dte: "99" }
  ];
  const labels = dteDateLabels(chainRows);
  const result = {
    choices: candidateDteValues("ZF"),
    labelKeys: Array.from(labels.keys()),
    oneDteDates: Array.from(labels.get(1) || [])
  };
  chainRows = savedChainRows;
  JSON.stringify(result);
`).runInContext(context);
const recalculatedDtes = JSON.parse(dteRecalculationResult);
assert.deepStrictEqual(recalculatedDtes.choices, [1], "expired rows must not be clamped into 0DTE choices");
assert.deepStrictEqual(recalculatedDtes.labelKeys, [1], "DTE headers must ignore stale CSV dte values and expired rows");
assert.deepStrictEqual(recalculatedDtes.oneDteDates, ["2026-07-10"], "DTE header date must follow the recalculated expiry DTE");

const parityDeltaResult = new vm.Script(`
  const parityRows = [
    { symbol: "ZF", expiry: "20260731", strike: "106.25", right: "C", delta: "0.74", bid: "0.70" },
    { symbol: "ZF", expiry: "20260731", strike: "106.25", right: "P", delta: "", bid: "0.12" }
  ];
  const info = candidateDeltaInfo(parityRows[1], "P", parityRows);
  const cell = chainEditableMapCell({
    id: "parity-put", underlying: "ZF", expiry: "20260731", right: "P", strike: 106.25,
    bid: 0.12, ask: 0.13, estimatedCredit: 0.12, multiplier: 1000, delta: info.value,
    deltaEstimated: info.estimated, marginEstimate: 1200, finalScore: 1, currentContracts: 0
  }, "put", false);
  JSON.stringify({ info, cell });
`).runInContext(context);
const parityDelta = JSON.parse(parityDeltaResult);
assert.strictEqual(parityDelta.info.estimated, true, "missing one-sided delta should use an explicitly estimated parity fallback");
assert(Math.abs(parityDelta.info.value + 0.26) < 1e-9, "put delta parity fallback should use call delta minus the parity factor");
assert(parityDelta.cell.includes("Delta≈-0.26"), "parity-estimated deltas must be visibly marked in candidate cells");

const premiumMetricResult = new vm.Script(`
  JSON.stringify({
    lowDelta: deltaWeightedPremium(100, -0.30),
    customThreshold: deltaWeightedPremium(100, -0.30, 0.25),
    displayedThresholdDelta: deltaWeightedPremium(100, -0.3952),
    thresholdDelta: deltaWeightedPremium(100, -0.40),
    highDelta: deltaWeightedPremium(100, 0.80)
  });
`).runInContext(context);
const premiumMetric = JSON.parse(premiumMetricResult);
assert.strictEqual(premiumMetric.lowDelta, 100, "low-delta premium should remain unadjusted");
assert(Math.abs(premiumMetric.customThreshold - 70) < 1e-9, "custom premium threshold should be applied immediately");
assert(Math.abs(premiumMetric.displayedThresholdDelta - 60.48) < 1e-9, "a delta displayed as 0.40 should enter the weighted metric using its actual value");
assert(Math.abs(premiumMetric.thresholdDelta - 60) < 1e-9, "0.40 delta premium should be weighted by 1-|delta|");
assert(Math.abs(premiumMetric.highDelta - 20) < 1e-9, "high-delta premium weighting is incorrect");

const highDeltaMarkerResult = new vm.Script(`
  const marked = chainReadonlyMapCell({
    underlying: "ZN", position: -1, contracts: 1, strike: 109.25, right: "C",
    delta: 0.3952, marketValue: -100, bid: 0.09, ask: 0.11
  }, "call", false, { markHighDelta: true, highDeltaThreshold: 0.40 });
  const unmarked = chainReadonlyMapCell({
    underlying: "ZN", position: -1, contracts: 1, strike: 109.25, right: "C",
    delta: 0.30, marketValue: -100, bid: 0.09, ask: 0.11
  }, "call", false, { markHighDelta: true, highDeltaThreshold: 0.40 });
  JSON.stringify({ marked, unmarked });
`).runInContext(context);
const highDeltaMarker = JSON.parse(highDeltaMarkerResult);
assert(highDeltaMarker.marked.includes("high-delta"), "a position displayed at the high-delta threshold should be highlighted");
assert(highDeltaMarker.marked.includes("高Delta ≥0.40"), "high-delta position should show the configured threshold badge");
assert(!highDeltaMarker.unmarked.includes("high-delta"), "a position below the configured threshold should not be highlighted");

const latestChainDeltaResult = new vm.Script(`
  const latestDeltaSavedChainRows = chainRows;
  chainRows = [
    { symbol: "ZN", conId: "777", expiry: "20260717", dte: "2", strike: "109.25", right: "C", delta: "0.3952", bid: "0.10" },
    { symbol: "ZN", conId: "888", expiry: "20260717", dte: "2", strike: "109.25", right: "C", delta: "0.22", bid: "0.09" }
  ];
  const cfg = { ...config(), underlying: "ZN", allowed: ["ZN"] };
  const positionDelta = enrichPositionQuote({
    conId: 777, underlying: "ZN", expiry: "20260717", dte: 2, strike: 109.25, right: "C",
    remainingPremium: 100, delta: 0.20, gamma: 0, theta: 0, vega: 0
  }, cfg);
  const chainFallback = enrichPositionQuote({
    conId: 777, underlying: "ZN", expiry: "20260717", dte: 2, strike: 109.25, right: "C",
    remainingPremium: 100, delta: 0, deltaObserved: false, gamma: 0, theta: 0, vega: 0
  }, cfg);
  chainRows = latestDeltaSavedChainRows;
  JSON.stringify({ positionDelta, chainFallback });
`).runInContext(context);
const latestChainDelta = JSON.parse(latestChainDeltaResult);
assert(Math.abs(latestChainDelta.positionDelta.delta - 0.20) < 1e-9, "an observed position delta must not be overwritten by an older chain snapshot");
assert.strictEqual(latestChainDelta.positionDelta.weightedPremium, 100, "the observed position delta should drive weighted premium");
assert(Math.abs(latestChainDelta.chainFallback.delta - 0.3952) < 1e-9, "a missing position delta should use the exact-conId chain delta");
assert(Math.abs(latestChainDelta.chainFallback.weightedPremium - 60.48) < 1e-9, "exact-conId fallback delta should drive weighted premium");

const snapshotFreshnessResult = new vm.Script(`
  const quoteRows = [
    { symbol: "ZN", snapshotTimeUtc: "2026-07-15T10:00:00Z" },
    { symbol: "ZN", snapshotTimeUtc: "2026-07-07T10:00:00Z" }
  ];
  const latest = latestProductSnapshotMs(quoteRows, "ZN");
  JSON.stringify({ fresh: candidateSnapshotIsCurrent(quoteRows[0], latest), stale: candidateSnapshotIsCurrent(quoteRows[1], latest) });
`).runInContext(context);
const snapshotFreshness = JSON.parse(snapshotFreshnessResult);
assert.strictEqual(snapshotFreshness.fresh, true, "latest candidate snapshot should remain eligible");
assert.strictEqual(snapshotFreshness.stale, false, "lagging candidate snapshot should be excluded");

const zcUnderlyingResult = new vm.Script(`
  const zcCfg = { ...config(), underlying: "ZC", allowed: ["ZC"], putStrikeMin: 424, callStrikeMax: 490 };
  const rowSpot = rowReferencePrice({ symbol: "ZC", undPrice: "4.655" }, "ZC", 443.5);
  JSON.stringify({ rowSpot, putIsOtm: strikeWithinOpenWindow(450, "P", rowSpot, zcCfg) });
`).runInContext(context);
const zcUnderlying = JSON.parse(zcUnderlyingResult);
assert.strictEqual(zcUnderlying.rowSpot, 465.5, "ZC row-level underlying price should normalize to cents");
assert.strictEqual(zcUnderlying.putIsOtm, true, "ZC OTM filtering should use the option row's own futures month");

const narrowedInventoryResult = new vm.Script(`
  document.getElementById("inventoryDtePreset").value = "near";
  render();
  JSON.stringify({
    planningDtes: Array.from(new Set(shortPositions.map(row => Math.round(row.dte)))),
    inventoryHtml: document.getElementById("inventoryChain").innerHTML
  });
`).runInContext(context);
const narrowedInventory = JSON.parse(narrowedInventoryResult);
assert(!narrowedInventory.planningDtes.includes(farthestLiveDte), "0-2DTE planning scope should omit farther inventory");
assert(narrowedInventory.inventoryHtml.includes(`${farthestLiveDte}DTE`), "inventory chain should retain holdings outside the planning scope");

const legacyZcResult = new vm.Script(`
  JSON.stringify(parseShortPositions([{
    symbol: "ZC", secType: "FOP", position: "-1", expiry: "20260717", strike: "4.25", right: "P",
    bid: "0.5", ask: "0.75", price: "0.625", marketValue: "-3125", valueSource: "estimated_from_market",
    multiplier: "5000", avgCost: "34.48", costBasis: "-34.48", delta: "-0.12"
  }], { ...config(), underlying: "ZC", allowed: ["ZC"], putInventoryDte: [0, 999], callInventoryDte: [0, 999] }, { respectInventoryDte: false }));
`).runInContext(context);
const legacyZc = JSON.parse(legacyZcResult)[0];
assert.strictEqual(legacyZc.multiplier, 50, "ZC planner multiplier should be USD 50 per cent");
assert.strictEqual(legacyZc.marketValue, -31.25, "legacy estimated ZC market value should be rescaled from cents");
assert(Math.abs(legacyZc.unrealizedPnL - 3.23) < 1e-9, "legacy estimated ZC PnL should be recomputed from the corrected value");

console.log("inventory planner dashboard smoke ok");
