const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const html = fs.readFileSync("sell_side_inventory_planner.html", "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/);
assert(script, "script not found");
assert(!html.includes('id="stressTable"'), "pressure test table should be removed");
assert(!html.includes("压力场景"), "pressure test section should be removed");
assert(html.includes('id="activeUnderlying"'), "product selector missing");
assert(html.includes('id="putZonePreset"'), "put zone dropdown missing");
assert(html.includes('id="inventoryChain"'), "inventory option-chain view missing");
assert(html.includes('id="greekDteTable"'), "DTE greek table missing");
assert(html.includes('id="priceChart"'), "future price chart missing");
assert(html.includes("candidate-combo-panel"), "candidate and combo area should be one panel");
assert(html.includes("sticky-side"), "combo side should stay sticky while chain scrolls");
assert(!html.includes("可覆盖提示"), "node warning side panel should be removed");
assert(html.includes('id="newDteChoices"'), "new open DTE choices missing");
assert(html.includes('id="putDeltaChoices"'), "put delta choices missing");
assert(html.includes('<option value="all" selected>全部库存</option>'), "inventory DTE should default to all");
assert(html.includes("data/clean_verify/ZF_FOP_Static_202609_202612_from_20260706_to_all_snapshot.csv"), "clean_verify ZF snapshot should be default source");

const ids = Array.from(html.matchAll(/id="([^"]+)"/g), match => match[1]);
const inputValues = Object.fromEntries(Array.from(html.matchAll(/<(?:input|select)[^>]*id="([^"]+)"[^>]*value="([^"]*)"/g), match => [match[1], match[2]]));

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

  querySelectorAll(selector) {
    if (selector === "input[type=checkbox]" || selector === "input[type=checkbox]:checked") {
      return Array.from(this.innerHTML.matchAll(/<input[^>]*type="checkbox"[^>]*value="([^"]+)"([^>]*)>/g), match => {
        if (selector.endsWith(":checked") && !/\schecked\b/.test(match[2])) return null;
        const id = match[1];
        const input = this.inputCache.get(`checkbox:${id}`) || new StubInput("", id);
        input.value = id;
        input.checked = /\schecked\b/.test(match[2]);
        this.inputCache.set(`checkbox:${id}`, input);
        return input;
      }).filter(Boolean);
    }
    if (selector !== "input[data-id]") return [];
    return Array.from(this.innerHTML.matchAll(/data-id="([^"]+)"/g), match => {
      const id = match[1];
      const input = this.inputCache.get(id) || new StubInput("", "0");
      input.dataset.id = id;
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
assert(elements.get("inventoryBars").innerHTML.includes("7DTE"), "expiry axis should include chain DTEs");
assert(elements.get("inventoryBars").innerHTML.includes("12DTE"), "expiry axis should include farther chain DTEs");
assert(!elements.get("inventoryBars").innerHTML.includes("5DTE"), "expiry axis should not invent non-chain DTEs");
assert(elements.get("greekSummary").innerHTML.includes("合约张数"), "greek summary missing");
assert(elements.get("greekSummary").innerHTML.includes("期货张数"), "future quantity summary missing");
assert(elements.get("futurePricePrompt").innerHTML.includes("手动期货价"), "manual future price input missing");
assert(elements.get("greekDteTable").innerHTML.includes("DTE分组"), "DTE greek table missing");
assert(!elements.get("greekDteTable").innerHTML.includes("6.000"), "contracts should render as integers");
assert(elements.get("inventoryChain").innerHTML.includes("Strike"), "inventory chain strike column missing");
assert(elements.get("candidateTable").innerHTML.includes("data-id="), "candidate quantity inputs missing");
assert(elements.get("candidateTable").innerHTML.includes("Call"), "candidate chain call side missing");
assert(elements.get("candidateTable").innerHTML.includes("Put"), "candidate chain put side missing");
assert(elements.get("candidateTable").innerHTML.includes("当前期货价"), "future price marker missing");
assert(elements.get("candidateTable").innerHTML.includes("Bid/Ask"), "candidate full quote header missing");
assert(!elements.get("candidateTable").innerHTML.includes("20260710"), "candidate chain should not show full expiry date");
assert(elements.get("beforeAfter").innerHTML.includes("当前"), "before/after table missing current row");
assert(elements.get("nodeTable").innerHTML.includes("106.500"), "short position strike missing");
assert(!elements.get("nodeTable").innerHTML.includes("ZF-P-105.500"), "long option leaked into core node table");

const qtyInput = elements.get("candidateTable").querySelectorAll("input[data-id]")[0];
assert(qtyInput, "quantity input listener missing");
qtyInput.value = "2";
qtyInput.dispatch("input");

assert(elements.get("beforeAfter").innerHTML.includes("新增"), "manual allocation did not update before/after");
assert(elements.get("selectedOptions").innerHTML.includes("× 2"), "manual allocation did not update selected list");
assert(elements.get("afterDteTable").innerHTML.includes("DTE分组"), "manual allocation did not update DTE table");

console.log("inventory planner dashboard smoke ok");
