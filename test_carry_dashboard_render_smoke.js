const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const html = fs.readFileSync("carry_risk_dashboard.html", "utf8");
const scriptMatch = html.match(/<script>([\s\S]*)<\/script>/);
assert(scriptMatch, "script not found");

const ids = Array.from(html.matchAll(/id="([^"]+)"/g), match => match[1]);
const initialValues = Object.fromEntries(
  Array.from(html.matchAll(/<input[^>]*id="([^"]+)"[^>]*value="([^"]*)"/g), match => [match[1], match[2]])
);

class StubButton {
  constructor(product) {
    this.dataset = { product };
    this.listeners = {};
  }

  addEventListener(type, callback) {
    this.listeners[type] = callback;
  }

  click() {
    if (this.listeners.click) this.listeners.click();
  }
}

class StubElement {
  constructor(id) {
    this.id = id;
    this.value = initialValues[id] || "";
    this.innerHTML = "";
    this.textContent = "";
    this.listeners = {};
    this.lastButtons = [];
    this.classList = {
      toggle: () => undefined
    };
  }

  addEventListener(type, callback) {
    this.listeners[type] = callback;
  }

  click() {
    if (this.listeners.click) this.listeners.click();
  }

  querySelectorAll(selector) {
    if (selector !== "button") return [];
    this.lastButtons = Array.from(this.innerHTML.matchAll(/data-product="([^"]+)"/g), match => new StubButton(match[1]));
    return this.lastButtons;
  }
}

const elements = new Map(ids.map(id => [id, new StubElement(id)]));
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new StubElement(id));
    return elements.get(id);
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
  Promise,
  setTimeout,
  clearTimeout,
  fetch: async () => ({ ok: false, status: 404, text: async () => "" })
});

new vm.Script(scriptMatch[1]).runInContext(context);

elements.get("sampleBtn").click();

assert(elements.get("productTabs").innerHTML.includes('data-product="ZF"'), "ZF tab missing");
assert(elements.get("productTabs").innerHTML.includes('data-product="ZN"'), "ZN tab missing");
assert(elements.get("productTabs").innerHTML.includes('data-product="ZC"'), "ZC tab missing");
assert(elements.get("dataStatus").innerHTML.includes("可切换 ZC / ZF / ZN"), "data status does not show product switch");
assert(elements.get("klineChart").innerHTML.includes("<svg"), "K-line chart did not render svg");
assert(elements.get("klineCaption").textContent.includes("ZF"), "ZF K-line caption missing");
assert(elements.get("optionChainBoard").innerHTML.includes("C Last"), "chain board call columns missing");
assert(elements.get("optionChainBoard").innerHTML.includes("P Vol"), "chain board put volume column missing");
assert.strictEqual(elements.get("sellMatrixNote").textContent, "使用当前新鲜标准期权链估算补卖候选。", "sell matrix did not use fresh chain");
assert(elements.get("decisionCards").innerHTML.includes("目标 $2,000"), "fixed target card missing");

const znTab = elements.get("productTabs").lastButtons.find(button => button.dataset.product === "ZN");
assert(znTab, "ZN tab listener missing");
znTab.click();

assert(elements.get("klineCaption").textContent.includes("ZN"), "ZN K-line caption missing after tab click");
assert(elements.get("chainBoardTitle").textContent.includes("ZN"), "ZN chain board title missing after tab click");
assert(elements.get("dataStatus").innerHTML.includes(">ZN<") || elements.get("dataStatus").innerHTML.includes("ZN"), "ZN data status missing");

const zcTab = elements.get("productTabs").lastButtons.find(button => button.dataset.product === "ZC");
assert(zcTab, "ZC tab listener missing");
zcTab.click();

assert(elements.get("klineCaption").textContent.includes("ZC"), "ZC K-line caption missing after tab click");
assert(elements.get("chainBoardTitle").textContent.includes("ZC"), "ZC chain board title missing after tab click");
assert(elements.get("dataStatus").innerHTML.includes(">ZC<") || elements.get("dataStatus").innerHTML.includes("ZC"), "ZC data status missing");

console.log("carry dashboard render smoke ok");
