const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const html = fs.readFileSync("A_Share_Option/dashboard.html", "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/);
assert(script, "script not found");

for (const id of [
  "readModeSelect",
  "productGrid",
  "optionMatrix",
  "positionProduct",
  "positionType",
  "positionExpiry",
  "positionStrike",
  "positionCode",
  "inventoryNodes",
]) {
  assert(html.includes(`id="${id}"`), `${id} missing`);
}
assert(html.includes("期权链矩阵"), "option matrix title missing");
assert(html.includes("当前库存行权节点"), "inventory nodes title missing");
assert(!html.includes('class="summary"'), "old summary cards should not be present");
assert(html.includes("async function loadChains()"), "all-product chain loader missing");
assert(html.includes("Object.entries(PRODUCT_SHEETS)"), "both configured product sheets should load together");
assert(html.includes("for (const [product, sheet] of Object.entries(PRODUCT_SHEETS))"), "Excel sheets should load serially");
assert(html.includes("setInterval(loadChains, seconds * 1000)"), "automatic refresh should call the active chain loader");
assert(!html.includes("loadChain(false)"), "automatic refresh must not call the removed loader");
assert(html.includes("function matrixColumns()"), "dense row-oriented option columns missing");
assert(html.includes("data-delete"), "position delete action missing");
assert(html.includes(">删除</button>"), "visible position delete button missing");
assert(html.includes("持仓笔数") && html.includes("可平市值"), "portfolio overview should use position metrics");
assert(html.includes("<th style=\"width:56px;\">Bid</th>") && html.includes("<th style=\"width:56px;\">Ask</th>"), "positions should show separate bid and ask columns");
assert(html.includes("sourceMode: \"live-cache\""), "saved fallback should preserve the last successful live prices");

class StubElement {
  constructor(id) {
    this.id = id;
    this.value = "";
    this.innerHTML = "";
    this.textContent = "";
    this.className = "";
    this.dataset = {};
    this.listeners = {};
  }
  addEventListener(type, callback) {
    this.listeners[type] = callback;
  }
}

const elementCache = new Map();
const documentStub = {
  addEventListener() {},
  getElementById(id) {
    if (!elementCache.has(id)) elementCache.set(id, new StubElement(id));
    return elementCache.get(id);
  },
  querySelectorAll() {
    return [];
  },
};

const context = {
  document: documentStub,
  console,
  setInterval() { return 1; },
  clearInterval() {},
  fetch() { throw new Error("fetch should not run during smoke load"); },
  Blob: class {},
  URL: { createObjectURL() { return ""; }, revokeObjectURL() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  window: { devicePixelRatio: 1 },
};
vm.createContext(context);
vm.runInContext(script[1], context, { filename: "dashboard.html" });

(async () => {
  vm.runInContext(`
    state.activeProduct = "\\u521b\\u4e1a";
    state.chain = [
      { rowKey: "call", code: "CALL", product: "\\u521b\\u4e1a", optionType: "\\u8ba4\\u8d2d", expiry: "9", strikeLabel: "3.4", underlyingPrice: 4.04, mark: 0.6759, last: 0.6759, bid: 0.6724, ask: 0.6794, change: 0.1125, changePct: 18.2, volume: 42, openInterest: 1453, iv: 0.72, miv: 0.68, delta: 0.9982, gamma: 0.0685, vega: 0.0002, theta: -0.0003, margin: 11825 },
      { rowKey: "put", code: "PUT", product: "\\u521b\\u4e1a", optionType: "\\u8ba4\\u6cbd", expiry: "9", strikeLabel: "3.4", underlyingPrice: 4.04, mark: 0.0022, last: 0.0022, bid: 0.002, ask: 0.0024, change: -0.0003, changePct: -12.5, volume: 587, openInterest: 42500, iv: 0.8489, miv: 0.8565, delta: -0.0026, gamma: 0.0012, vega: 0.0001, theta: -0.0001, margin: 4200 }
    ];
    state.positions = [{ id: "delete-me", product: "\\u521b\\u4e1a", optionType: "\\u8ba4\\u6cbd", expiry: "9", strike: "3.4", code: "PUT", quantity: 1, openPrice: 0.0025 }];
    $("productFilter").value = "\\u521b\\u4e1a";
    $("matrixExpiryFilter").value = "";
    $("searchInput").value = "";
    $("matrixView").value = "quote";
    $("multiplierInput").value = "10000";
    renderOptionMatrix();
  `, context);
  const matrix = elementCache.get("optionMatrix").innerHTML;
  assert(matrix.includes('colspan="9"'), "option features should render across one row");
  assert(matrix.includes("最新价"), "quote columns missing");
  assert(matrix.includes("Bid") && matrix.includes("Ask"), "executable bid and ask columns missing");
  assert(!matrix.includes("合约"), "contract column should not render in the option chain");

  const executablePricing = vm.runInContext(`
    [
      enrichPosition({ product: "\\u521b\\u4e1a", optionType: "\\u8ba4\\u6cbd", expiry: "9", strike: "3.4", code: "PUT", quantity: 1, openPrice: 0.0025 }),
      enrichPosition({ product: "\\u521b\\u4e1a", optionType: "\\u8ba4\\u8d2d", expiry: "9", strike: "3.4", code: "CALL", quantity: -1, openPrice: 0.6759 })
    ].map((position) => ({ exitPrice: position.exitPrice, pnl: position.pnl }))
  `, context);
  assert.strictEqual(executablePricing[0].exitPrice, 0.002, "long positions should close at bid");
  assert.strictEqual(executablePricing[1].exitPrice, 0.6794, "short positions should close at ask");
  assert(Math.abs(executablePricing[0].pnl + 5) < 1e-8, "long PnL should use bid");
  assert(Math.abs(executablePricing[1].pnl + 35) < 1e-8, "short PnL should use ask");

  await vm.runInContext('deletePosition("delete-me")', context);
  const remaining = vm.runInContext('state.positions.length', context);
  assert.strictEqual(remaining, 0, "delete should update the visible position state immediately");
  console.log("dashboard smoke ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
