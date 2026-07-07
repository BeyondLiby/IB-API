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
assert(html.includes('id="refreshProgress"'), "refresh progress bar missing");
assert(!html.includes('id="putZonePreset"'), "old put zone dropdown should be removed");
assert(html.includes('id="inventoryChain"'), "inventory option-chain view missing");
assert(html.includes('id="productOverview"'), "product overview missing");
assert(html.includes('id="greekDteTable"'), "DTE greek table missing");
assert(html.includes('id="dailyPriceChart"'), "daily future price chart missing");
assert(html.includes('id="intradayPriceChart"'), "intraday future price chart missing");
assert(html.includes('id="chartRange"'), "chart range control missing");
assert(html.includes('id="chartZoomIn"'), "chart zoom control missing");
assert(html.includes("candidate-combo-panel"), "candidate and combo area should be one panel");
assert(html.includes("sticky-side"), "combo side should stay sticky while chain scrolls");
assert(!html.includes("可覆盖提示"), "node warning side panel should be removed");
assert(html.includes('id="putStrikeMin"'), "put strike selector missing");
assert(html.includes('id="callStrikeMax"'), "call strike selector missing");
assert(html.includes('id="putDeltaChoices"'), "put delta choices missing");
assert(html.includes('id="chainDteChoices"'), "chain DTE selector missing");
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
    this.listeners = {};
  }

  addEventListener(type, callback) {
    this.listeners[type] = callback;
  }

  dispatch(type = "input") {
    if (this.listeners[type]) this.listeners[type]();
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

const context = vm.createContext({
  console,
  document,
  Date,
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

elements.get("loadSample").click();

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
assert(elements.get("productOverview").innerHTML.includes("期货"), "product overview should show futures quantity separately");
assert(elements.get("productOverview").innerHTML.includes("期权Delta"), "product overview delta should be explicitly option-only");
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
assert(elements.get("beforeAfter").innerHTML.includes("当前"), "before/after table missing current row");
assert(elements.get("nodeTable").innerHTML.includes("106.500"), "short position strike missing");
assert(!elements.get("nodeTable").innerHTML.includes("ZF-P-105.500"), "long option leaked into core node table");

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
    selectedChainDtes: config().selectedChainDtes,
    chainDteHtml: document.getElementById("chainDteChoices").innerHTML,
    futurePrompt: document.getElementById("futurePricePrompt").innerHTML,
    putDeltaHtml: document.getElementById("putDeltaChoices").innerHTML,
    creditSource: document.getElementById("creditSource").value,
    inventoryHtml: document.getElementById("inventoryChain").innerHTML,
    candidateHtml: document.getElementById("candidateTable").innerHTML,
    nodeHtml: document.getElementById("nodeTable").innerHTML,
    currentOverlayCount: shortPositions.filter(position => candidates.some(row => optionIdentityMatches(row, position) && currentContracts(row) > 0)).length,
    shortPositionCount: shortPositions.length,
    has0DteCallOverlay: candidates.some(row => row.right === "C" && Math.max(0, Math.round(row.dte)) === 0 && currentContracts(row) > 0),
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
assert.strictEqual(live.shortByDte["0"], 5, "0DTE live inventory should include five current contracts");
assert.strictEqual(live.shortByDte["1"], 4, "1DTE live inventory should include four current contracts");
assert.strictEqual(live.remainingTarget, Math.max(live.monthlyTarget - live.currentPremium, 0), "target pressure should deduct current remaining premium");
assert(live.remainingTarget < live.monthlyTarget, "remaining target should be lower than gross monthly target when premium exists");
assert(live.putStrikeOptions.includes(live.putStrikeMin), "put strike selector should default to a real chain strike");
assert(live.callStrikeOptions.includes(live.callStrikeMax), "call strike selector should default to a real chain strike");
assert(live.putStrikeMin < 106.914, "put strike selector should default below the ZF spot");
assert(live.callStrikeMax > 106.914, "call strike selector should default above the ZF spot");
assert(live.chainDteHtml.includes('type="checkbox"'), "chain DTE selector should render checkboxes");
assert(live.selectedChainDtes.length > 0, "chain DTE selector should choose default DTEs");
assert(live.selectedChainDtes.every(dte => dte <= 10), "chain DTE selector should default to near expiries only");
assert.strictEqual(live.creditSource, "bid", "sell-side credit source should default to bid");
assert(/chain|positions|future-position/.test(live.futurePrompt), "future price should come from planner data");
assert(/10\d\.\d+/.test(live.futurePrompt), "future price prompt should show a numeric ZF price");
assert(live.putDeltaHtml.includes('type="radio"'), "delta limit should render as a single-choice radio group");
assert(!live.putDeltaHtml.includes('type="checkbox"'), "delta limit should not render multiple checkboxes");
assert(live.putDeltaHtml.includes('value="0.5"'), "delta limit should allow 0.50");
assert(/\d+\.\d+\s\/\s\d+\.\d+/.test(live.inventoryHtml), "inventory chain should show bid/ask quotes from the option chain");
assert(/-\d+ 张 zf \d/.test(live.inventoryHtml), "inventory chain should use compact signed position names");
assert(live.inventoryHtml.includes("chain-map"), "inventory chain should render as a horizontal option-chain map");
assert(live.candidateHtml.includes("chain-map"), "candidate chain should render as a horizontal option-chain map");
assert(live.nodeHtml.includes("chain-map"), "adjusted node view should render as a horizontal option-chain map");
assert(live.inventoryHtml.includes("DTE Call") && live.inventoryHtml.includes("DTE Put"), "inventory map should separate call and put sides by DTE");
assert(/chain-spot-line put/.test(live.candidateHtml) && /chain-spot-line call/.test(live.candidateHtml), "spot row should repeat DTE labels for scrolled columns");
assert(/Delta [^<]+ · 市值/.test(live.inventoryHtml), "inventory map should place market value beside delta");
assert(live.candidateHtml.includes("title-income"), "candidate map should show actual premium income next to the option name");
assert(/title-income">\+\$/.test(live.candidateHtml), "candidate map income should be placed immediately after the option name");
assert(live.inventoryHtml.includes("invalid-zone"), "inventory map should whiten empty cells on the wrong side of spot");
assert(!live.candidateHtml.includes("Bid/Ask - /"), "candidate map should exclude rows without a sellable bid");
assert(live.candidateHtml.includes("has-position"), "candidate map should mark cells that already have inventory");
assert(/当前 \d+张/.test(live.candidateHtml), "candidate map should show current inventory quantity");
assert(/has-position[\s\S]*value="[1-9]\d*"/.test(live.candidateHtml), "existing inventory candidate input should default to current quantity");
assert.strictEqual(live.currentOverlayCount, live.shortPositionCount, "candidate rows should include every current inventory position as an overlay");
assert(live.has0DteCallOverlay, "candidate rows should include current 0DTE call inventory");
assert(live.candidateStrikeWindowOk, "openable candidate rows should respect the strike-distance window");
assert(!live.inventoryHtml.includes("strike-meta"), "inventory strike cells should not repeat DTE");
assert(!live.candidateHtml.includes("strike-meta"), "candidate strike cells should not repeat DTE");

console.log("inventory planner dashboard smoke ok");
