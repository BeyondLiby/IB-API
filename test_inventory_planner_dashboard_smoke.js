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
    selectedCandidateDtes: config().selectedCandidateDtes,
    candidateDteHtml: document.getElementById("candidateDteChoices").innerHTML,
    futurePrompt: document.getElementById("futurePricePrompt").innerHTML,
    putDeltaHtml: document.getElementById("putDeltaChoices").innerHTML,
    creditSource: document.getElementById("creditSource").value,
    inventoryHtml: document.getElementById("inventoryChain").innerHTML,
    candidateHtml: document.getElementById("candidateTable").innerHTML,
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
assert(live.candidateHtml.includes("chain-map"), "candidate chain should render as a horizontal option-chain map");
assert(live.nodeHtml.includes("chain-map"), "adjusted node view should render as a horizontal option-chain map");
assert(live.inventoryHtml.includes("DTE Call") && live.inventoryHtml.includes("DTE Put"), "inventory map should separate call and put sides by DTE");
assert(/chain-spot-line put/.test(live.candidateHtml) && /chain-spot-line call/.test(live.candidateHtml), "spot row should repeat DTE labels for scrolled columns");
assert(live.inventoryHtml.includes("title-value"), "inventory map should place market value beside the option name");
assert(live.candidateHtml.includes("title-income"), "candidate map should show actual premium income next to the option name");
assert(/title-income">\+\$/.test(live.candidateHtml), "candidate map income should be placed immediately after the option name");
assert(live.inventoryHtml.includes("invalid-zone"), "inventory map should whiten empty cells on the wrong side of spot");
assert(!live.candidateHtml.includes("Bid/Ask - /"), "candidate map should exclude rows without a sellable bid");
assert(live.candidateStrikeWindowOk, "openable candidate rows should respect the strike-distance window");
assert(!live.inventoryHtml.includes("strike-meta"), "inventory strike cells should not repeat DTE");
assert(!live.candidateHtml.includes("strike-meta"), "candidate strike cells should not repeat DTE");

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
