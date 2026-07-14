const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync('A_Share_Option/arbitrage_monitor.html', 'utf8');
for (const text of [
  'ETF Conversion 套利监控', '买 ETF + 买 Put - 卖 Call', 'underlyingBid', 'underlyingAsk',
  'function opportunities()', 'source:"手工 Bid/Ask"', 'source:"现券最新价估算"', 'marginMode', 'setInterval(loadChains',
  'optionFee', 'etfFeeBps', 'marginRate', 'optionOpenFee=2*optionFee', 'etfOpenFee=q.ask*unit*etfRate', 'const update=()=>{saveSettings();render()}', 'window.location.protocol==="file:"', 'http://127.0.0.1:8777/arbitrage_monitor.html',
]) assert(html.includes(text), `missing ${text}`);
const script = html.match(/<script>([\s\S]*)<\/script>/);
assert(script, 'script missing');
new Function(script[1]);

class StubElement {
  constructor() { this.value = ''; this.innerHTML = ''; this.textContent = ''; this.className = ''; this.options = []; }
  addEventListener() {}
}
const elements = new Map();
const documentStub = {
  addEventListener() {},
  getElementById(id) { if (!elements.has(id)) elements.set(id, new StubElement()); return elements.get(id); },
};
const context = { document: documentStub, localStorage: { getItem() { return null; }, setItem() {} }, setInterval() { return 1; }, clearInterval() {}, fetch() { throw new Error('not used'); } };
vm.createContext(context);
vm.runInContext(script[1], context, { filename: 'arbitrage_monitor.html' });
vm.runInContext(`
  $("product").value = "\\u521b\\u4e1a";
  $("etfBid").value = "4";
  $("etfAsk").value = "4.01";
  $("unit").value = "10000";
  $("optionFee").value = "1.2";
  $("etfFeeBps").value = "1";
  $("fundRate").value = "0";
  $("marginRate").value = "0";
  $("marginMode").value = "covered";
  state.chain = [
    { product: "\\u521b\\u4e1a", optionType: "\\u8ba4\\u8d2d", expiry: "10", dte: 10, strike: 4.1, strikeLabel: "4.1", bid: 0.1, ask: 0.101, volume: 20, openInterest: 100, margin: 5000 },
    { product: "\\u521b\\u4e1a", optionType: "\\u8ba4\\u6cbd", expiry: "10", dte: 10, strike: 4.1, strikeLabel: "4.1", bid: 0.089, ask: 0.09, volume: 15, openInterest: 90 }
  ];
`, context);
const result = JSON.parse(vm.runInContext('JSON.stringify(opportunities()[0])', context));
assert(Math.abs(result.entryFee - 6.41) < 1e-8, 'entry fee should be ETF ask notional at 1 bp plus two option fees');
assert(Math.abs(result.entry - 40006.41) < 1e-8, 'entry cost should use ETF ask, put ask, and call bid');
assert(Math.abs(result.net - 993.59) < 1e-8, 'expiry net profit should include stated fees');
assert(Math.abs(result.unwind + 132.81) < 1e-8, 'unwind PnL should use ETF bid, put bid, call ask, and closing fees');
vm.runInContext('$("etfBid").value=""; $("etfAsk").value=""; state.chain[0].underlyingPrice=4.02', context);
const estimatedQuote = JSON.parse(vm.runInContext('JSON.stringify(quotes(productRows()))', context));
assert.strictEqual(estimatedQuote.source, '现券最新价估算', 'last price should provide a labelled estimate when ETF bid and ask are zero');
assert.strictEqual(estimatedQuote.executable, false, 'last-price estimate must not be a tradable signal');
console.log('arbitrage monitor smoke ok');
