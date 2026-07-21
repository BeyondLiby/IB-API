import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.resolve(".");
const projectRoot = path.resolve(outputDir, "..", "..");
const runsDir = path.join(projectRoot, "data", "runs");
const observationPath = path.join(projectRoot, "data", "jpm_schedule_observations.json");
const asOf = new Date(2026, 6, 20);
const dayMs = 24 * 60 * 60 * 1000;

function dateFromIso(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function isoDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

const weekdayNames = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function dominantWeekday(dates) {
  const counts = new Map();
  for (const date of dates) counts.set(date.getDay(), (counts.get(date.getDay()) ?? 0) + 1);
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0]);
  return ranked.length ? weekdayNames[ranked[0][0]] : "样本不足";
}

function nextBusinessDay(value) {
  const result = new Date(value);
  do result.setDate(result.getDate() + 1);
  while (result.getDay() === 0 || result.getDay() === 6);
  return result;
}

function estimateNext(latest, cadenceDays, isBusinessDaily = false) {
  if (isBusinessDaily) return nextBusinessDay(asOf);
  const elapsed = Math.floor((asOf - latest) / dayMs);
  const periods = Math.floor(elapsed / cadenceDays) + 1;
  const result = new Date(latest);
  result.setDate(result.getDate() + periods * cadenceDays);
  while (result.getDay() === 0 || result.getDay() === 6) result.setDate(result.getDate() + 1);
  if (result <= asOf) return nextBusinessDay(asOf);
  return result;
}

const runFiles = (await fs.readdir(runsDir)).filter((name) => /^jpm_.*\.json$/i.test(name));
let fullRun = null;
let sourceRunName = null;
for (const name of runFiles) {
  const payload = JSON.parse(await fs.readFile(path.join(runsDir, name), "utf8"));
  if (!fullRun || (payload.items?.length ?? 0) > (fullRun.items?.length ?? 0)) {
    fullRun = payload;
    sourceRunName = name;
  }
}
if (!fullRun || !fullRun.items?.length) throw new Error("No JPM run data found");

const observations = JSON.parse(await fs.readFile(observationPath, "utf8"));
const itemsPerMonthMap = new Map(
  observations.subscriptions.map((entry) => [entry.subscription, entry.items_per_month]),
);

const grouped = new Map();
for (const item of fullRun.items) {
  if (!grouped.has(item.subscription)) grouped.set(item.subscription, []);
  grouped.get(item.subscription).push(item);
}

const scheduleRows = [];
for (const [subscription, items] of grouped.entries()) {
  const distinctIsoDates = [...new Set(items.map((item) => item.published_date))].sort();
  const dates = distinctIsoDates.map(dateFromIso);
  const intervals = [];
  for (let i = 1; i < dates.length; i += 1) {
    intervals.push(Math.round((dates[i] - dates[i - 1]) / dayMs));
  }
  const medianInterval = median(intervals);
  const weeklyShare = intervals.length
    ? intervals.filter((days) => Math.abs(days - 7) <= 1).length / intervals.length
    : 0;
  const itemsPerMonth = Number(itemsPerMonthMap.get(subscription) ?? 0);
  const isAnalyst = subscription === "Joseph Cardoso";
  const isWeekly = !isAnalyst && dates.length >= 5 && weeklyShare >= 0.55;
  let cadenceDays;
  let frequency;
  let method;
  if (isAnalyst || itemsPerMonth >= 20) {
    cadenceDays = 1;
    frequency = `高频（约${itemsPerMonth}次/月）`;
    method = "按下一个工作日推算";
  } else if (isWeekly) {
    cadenceDays = 7;
    frequency = "每周";
    method = "按最近样本的周度节奏推算";
  } else if (itemsPerMonth >= 8) {
    cadenceDays = Math.max(1, Math.round(30.44 / itemsPerMonth));
    frequency = `约每${cadenceDays}天（${itemsPerMonth}次/月）`;
    method = "按页面 Items/Month 换算";
  } else {
    cadenceDays = Math.max(1, Math.round(30.44 / Math.max(itemsPerMonth, 1)));
    frequency = itemsPerMonth === 1
      ? "约每月"
      : itemsPerMonth === 2
        ? "约每半月"
        : `约${itemsPerMonth}次/月`;
    method = "按页面 Items/Month 换算并滚动至基准日之后";
  }

  let confidence;
  if (isWeekly && weeklyShare >= 0.65) confidence = "高";
  else if (isAnalyst || itemsPerMonth >= 8) confidence = "中";
  else {
    const mismatch = medianInterval == null
      ? 1
      : Math.abs(medianInterval - cadenceDays) / cadenceDays;
    confidence = dates.length >= 5 && mismatch <= 0.5 ? "中" : "低";
  }

  const latest = dates[dates.length - 1];
  const predicted = estimateNext(latest, cadenceDays, isAnalyst || itemsPerMonth >= 20);
  const note = confidence === "低"
    ? "历史间隔波动较大；仅作提醒，非官方日程"
    : `${method}；非官方日程`;
  scheduleRows.push({
    subscription,
    category: isAnalyst ? "Analyst" : "Publication",
    latest,
    predicted,
    frequency,
    itemsPerMonth,
    cadenceDays,
    medianInterval,
    commonWeekday: dominantWeekday(dates),
    sampleCount: items.length,
    confidence,
    note,
  });
}
scheduleRows.sort((a, b) => a.category.localeCompare(b.category) || a.subscription.localeCompare(b.subscription));

const historicalRows = [...fullRun.items]
  .sort((a, b) => b.published_date.localeCompare(a.published_date) || a.subscription.localeCompare(b.subscription))
  .map((item) => ({
    subscription: item.subscription,
    category: item.subscription === "Joseph Cardoso" ? "Analyst" : "Publication",
    publishedDate: dateFromIso(item.published_date),
    reportType: item.title.split(/[:：]/, 1)[0].trim(),
    title: item.title,
    externalId: item.external_id,
    sourceUrl: item.url,
  }));

const workbook = Workbook.create();
const schedule = workbook.worksheets.add("更新日历");
const upcoming = workbook.worksheets.add("未来日历");
const history = workbook.worksheets.add("历史样本");
const method = workbook.worksheets.add("方法说明");

const colors = {
  brown: "#3B2314",
  brown2: "#6A4632",
  blue: "#4F81BD",
  paleBlue: "#DCE6F1",
  paleBrown: "#F2ECE8",
  line: "#D9D2CC",
  green: "#E2F0D9",
  yellow: "#FFF2CC",
  red: "#FCE4D6",
  white: "#FFFFFF",
  text: "#1F1F1F",
  muted: "#666666",
};

for (const sheet of [schedule, upcoming, history, method]) sheet.showGridLines = false;

// Main schedule sheet.
schedule.getRange("A1:L1").merge();
schedule.getRange("A1").values = [["JPM Research 更新日历"]];
schedule.getRange("A1:L1").format = {
  fill: colors.brown,
  font: { bold: true, color: colors.white, size: 18 },
  horizontalAlignment: "left",
  verticalAlignment: "center",
};
schedule.getRange("A1:L1").format.rowHeight = 34;
schedule.getRange("A2:L2").merge();
schedule.getRange("A2").values = [["基于 My Research Subscriptions 可见历史与页面 Items/Month 的统计推算；预计日期不是 JPM 官方发布日期承诺。"]];
schedule.getRange("A2:L2").format = {
  fill: colors.paleBrown,
  font: { color: colors.muted, italic: true },
  wrapText: true,
};

schedule.getRange("A3:K3").values = [[
  "基准日期", asOf, null, "订阅栏目", null, null, "未来7天预计", null, null, "高置信度", null,
]];
schedule.getRange("E3").formulas = [["=COUNTA(A8:A18)"]];
schedule.getRange("H3").formulas = [["=COUNTIFS(D8:D18,\">\"&B3,D8:D18,\"<=\"&B3+7)"]];
schedule.getRange("K3").formulas = [["=COUNTIF(K8:K18,\"高\")"]];
for (const rangeAddress of ["A3:B3", "D3:E3", "G3:H3", "J3:K3"]) {
  schedule.getRange(rangeAddress).format = {
    fill: colors.paleBlue,
    font: { bold: true, color: colors.brown },
    borders: { preset: "outside", style: "thin", color: colors.line },
  };
}
schedule.getRange("B3").format.numberFormat = "yyyy-mm-dd";
schedule.getRange("E3:H3").format.horizontalAlignment = "center";
schedule.getRange("K3").format.horizontalAlignment = "center";

const scheduleHeaders = [[
  "报告类型", "类别", "当前更新日期", "下一次预计更新日期", "推断更新频率",
  "页面 Items/月", "推算周期(天)", "历史中位间隔(天)", "常见更新日",
  "历史样本数", "置信度", "说明",
]];
schedule.getRange("A7:L7").values = scheduleHeaders;
const scheduleValues = scheduleRows.map((row) => [
  row.subscription,
  row.category,
  row.latest,
  null,
  row.frequency,
  row.itemsPerMonth,
  row.cadenceDays,
  row.medianInterval,
  row.commonWeekday,
  row.sampleCount,
  row.confidence,
  row.note,
]);
schedule.getRange(`A8:L${7 + scheduleRows.length}`).values = scheduleValues;
for (let row = 8; row <= 7 + scheduleRows.length; row += 1) {
  schedule.getRange(`D${row}`).formulas = [[
    `=WORKDAY(C${row}+G${row}*(INT(($B$3-C${row})/G${row})+1)-1,1)`,
  ]];
}
schedule.getRange(`C8:D${7 + scheduleRows.length}`).format.numberFormat = "yyyy-mm-dd";
schedule.getRange(`F8:J${7 + scheduleRows.length}`).format.horizontalAlignment = "center";
schedule.getRange(`K8:K${7 + scheduleRows.length}`).format.horizontalAlignment = "center";
schedule.getRange(`A7:L${7 + scheduleRows.length}`).format.borders = {
  insideHorizontal: { style: "thin", color: colors.line },
  bottom: { style: "thin", color: colors.line },
};
schedule.getRange("A7:L7").format = {
  fill: colors.brown2,
  font: { bold: true, color: colors.white },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "outside", style: "thin", color: colors.brown },
};
schedule.getRange("A7:L7").format.rowHeight = 32;
const scheduleTable = schedule.tables.add(`A7:L${7 + scheduleRows.length}`, true, "JpmScheduleTable");
scheduleTable.style = "TableStyleMedium2";
scheduleTable.showFilterButton = true;
schedule.getRange(`D8:D${7 + scheduleRows.length}`).conditionalFormats.addCustom(
  "=AND($D8>$B$3,$D8<=$B$3+7)",
  { fill: colors.green, font: { bold: true, color: "#375623" } },
);
schedule.getRange(`K8:K${7 + scheduleRows.length}`).conditionalFormats.add("containsText", {
  text: "高", format: { fill: colors.green, font: { bold: true, color: "#375623" } },
});
schedule.getRange(`K8:K${7 + scheduleRows.length}`).conditionalFormats.add("containsText", {
  text: "中", format: { fill: colors.yellow, font: { bold: true, color: "#7F6000" } },
});
schedule.getRange(`K8:K${7 + scheduleRows.length}`).conditionalFormats.add("containsText", {
  text: "低", format: { fill: colors.red, font: { bold: true, color: "#9C0006" } },
});
schedule.freezePanes.freezeRows(7);
schedule.freezePanes.freezeColumns(1);
schedule.getRange("A:A").format.columnWidth = 38;
schedule.getRange("B:B").format.columnWidth = 13;
schedule.getRange("C:D").format.columnWidth = 16;
schedule.getRange("E:E").format.columnWidth = 23;
schedule.getRange("F:J").format.columnWidth = 14;
schedule.getRange("K:K").format.columnWidth = 10;
schedule.getRange("L:L").format.columnWidth = 42;
schedule.getRange(`A8:L${7 + scheduleRows.length}`).format.wrapText = true;
schedule.getRange(`A8:L${7 + scheduleRows.length}`).format.rowHeight = 32;

// Upcoming calendar sheet.
upcoming.getRange("A1:G1").merge();
upcoming.getRange("A1").values = [["未来一次更新日历"]];
upcoming.getRange("A1:G1").format = {
  fill: colors.brown,
  font: { bold: true, color: colors.white, size: 18 },
};
upcoming.getRange("A1:G1").format.rowHeight = 34;
upcoming.getRange("A2:G2").merge();
upcoming.getRange("A2").values = [[`基准日期 ${isoDate(asOf)}；按预计日期排序。`]];
upcoming.getRange("A2:G2").format = { fill: colors.paleBrown, font: { color: colors.muted } };
upcoming.getRange("A4:G4").values = [[
  "下一次预计更新日期", "星期", "报告类型", "类别", "推断更新频率", "置信度", "说明",
]];
const upcomingRows = [...scheduleRows]
  .sort((a, b) => a.predicted - b.predicted || a.subscription.localeCompare(b.subscription));
upcoming.getRange(`A5:G${4 + upcomingRows.length}`).values = upcomingRows.map((row) => [
  row.predicted,
  weekdayNames[row.predicted.getDay()],
  row.subscription,
  row.category,
  row.frequency,
  row.confidence,
  row.note,
]);
upcoming.getRange(`A5:A${4 + upcomingRows.length}`).format.numberFormat = "yyyy-mm-dd";
upcoming.getRange("A4:G4").format = {
  fill: colors.brown2,
  font: { bold: true, color: colors.white },
  horizontalAlignment: "center",
  wrapText: true,
};
const upcomingTable = upcoming.tables.add(`A4:G${4 + upcomingRows.length}`, true, "UpcomingCalendarTable");
upcomingTable.style = "TableStyleMedium2";
upcoming.getRange(`F5:F${4 + upcomingRows.length}`).conditionalFormats.add("containsText", {
  text: "高", format: { fill: colors.green, font: { bold: true, color: "#375623" } },
});
upcoming.getRange(`F5:F${4 + upcomingRows.length}`).conditionalFormats.add("containsText", {
  text: "中", format: { fill: colors.yellow, font: { bold: true, color: "#7F6000" } },
});
upcoming.getRange(`F5:F${4 + upcomingRows.length}`).conditionalFormats.add("containsText", {
  text: "低", format: { fill: colors.red, font: { bold: true, color: "#9C0006" } },
});
upcoming.freezePanes.freezeRows(4);
upcoming.getRange("A:A").format.columnWidth = 20;
upcoming.getRange("B:B").format.columnWidth = 10;
upcoming.getRange("C:C").format.columnWidth = 42;
upcoming.getRange("D:D").format.columnWidth = 13;
upcoming.getRange("E:E").format.columnWidth = 24;
upcoming.getRange("F:F").format.columnWidth = 10;
upcoming.getRange("G:G").format.columnWidth = 45;
upcoming.getRange(`A5:G${4 + upcomingRows.length}`).format.wrapText = true;
upcoming.getRange(`A5:G${4 + upcomingRows.length}`).format.rowHeight = 30;

// Historical evidence sheet.
history.getRange("A1:G1").merge();
history.getRange("A1").values = [["历史发布日期样本（100条）"]];
history.getRange("A1:G1").format = {
  fill: colors.brown,
  font: { bold: true, color: colors.white, size: 17 },
};
history.getRange("A1:G1").format.rowHeight = 34;
history.getRange("A3:G3").values = [[
  "订阅栏目", "类别", "发布日期", "报告类型（标题首段）", "完整标题", "文档ID", "来源链接",
]];
history.getRange(`A4:G${3 + historicalRows.length}`).values = historicalRows.map((row) => [
  row.subscription,
  row.category,
  row.publishedDate,
  row.reportType,
  row.title,
  row.externalId,
  row.sourceUrl,
]);
history.getRange(`C4:C${3 + historicalRows.length}`).format.numberFormat = "yyyy-mm-dd";
history.getRange("A3:G3").format = {
  fill: colors.brown2,
  font: { bold: true, color: colors.white },
  horizontalAlignment: "center",
  wrapText: true,
};
const historyTable = history.tables.add(`A3:G${3 + historicalRows.length}`, true, "HistoricalSamplesTable");
historyTable.style = "TableStyleMedium2";
history.freezePanes.freezeRows(3);
history.getRange("A:A").format.columnWidth = 40;
history.getRange("B:B").format.columnWidth = 13;
history.getRange("C:C").format.columnWidth = 14;
history.getRange("D:D").format.columnWidth = 28;
history.getRange("E:E").format.columnWidth = 70;
history.getRange("F:F").format.columnWidth = 18;
history.getRange("G:G").format.columnWidth = 55;
history.getRange(`A4:G${3 + historicalRows.length}`).format.wrapText = true;
history.getRange(`A4:G${3 + historicalRows.length}`).format.rowHeight = 30;

// Method and audit sheet.
method.getRange("A1:B1").merge();
method.getRange("A1").values = [["推算口径与限制"]];
method.getRange("A1:B1").format = {
  fill: colors.brown,
  font: { bold: true, color: colors.white, size: 17 },
};
method.getRange("A1:B1").format.rowHeight = 34;
const methodRows = [
  ["项目", "内容"],
  ["基准日期", asOf],
  ["页面来源", "https://markets.jpmorgan.com/jpmm/research.my_subscriptions"],
  ["历史来源", `本地运行 ${sourceRunName}；每个 subscription 最近最多 10 篇可见样本`],
  ["页面频率", `逐项点击 10 个 Publications 与 1 个 Analyst，读取 Items/Month；观察时间 ${observations.observed_at_utc}`],
  ["当前更新日期", "每个栏目可见历史中的最新发布日期"],
  ["稳定周报", "最近样本中 7±1 天间隔占比达到 55% 时，按 7 天周期推算"],
  ["高频 Analyst", "Items/Month ≥ 20 时，下一次日期按基准日后的下一个工作日推算"],
  ["其他栏目", "按 30.44 / Items/Month 换算周期，从最新日期滚动到基准日之后；周末顺延至工作日"],
  ["置信度：高", "周度规律稳定且历史样本支持"],
  ["置信度：中", "页面频率与近期样本大致一致，或属于高频栏目"],
  ["置信度：低", "历史间隔波动较大，下一日期仅作为检查提醒"],
  ["重要限制", "预计日期是统计推断，不是 JPM 官方日程；临时专题、假期和市场事件会改变发布时间"],
];
method.getRange(`A3:B${2 + methodRows.length}`).values = methodRows;
method.getRange("A3:B3").format = {
  fill: colors.brown2,
  font: { bold: true, color: colors.white },
};
method.getRange(`A4:A${2 + methodRows.length}`).format = {
  fill: colors.paleBlue,
  font: { bold: true, color: colors.brown },
};
method.getRange(`A3:B${2 + methodRows.length}`).format.borders = {
  insideHorizontal: { style: "thin", color: colors.line },
  outside: { style: "thin", color: colors.line },
};
method.getRange("B4").format.numberFormat = "yyyy-mm-dd";
method.getRange("A:A").format.columnWidth = 24;
method.getRange("B:B").format.columnWidth = 100;
method.getRange(`A3:B${2 + methodRows.length}`).format.wrapText = true;
method.getRange(`A3:B${2 + methodRows.length}`).format.rowHeight = 34;

const scheduleInspect = await workbook.inspect({
  kind: "table",
  range: "更新日历!A1:L18",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 12,
});
console.log(scheduleInspect.ndjson);
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 200 },
  summary: "final formula error scan",
});
console.log(formulaErrors.ndjson);

for (const [sheetName, range, filename] of [
  ["更新日历", "A1:L18", "preview_schedule.png"],
  ["未来日历", "A1:G15", "preview_upcoming.png"],
  ["历史样本", "A1:G25", "preview_history.png"],
  ["方法说明", "A1:B15", "preview_method.png"],
]) {
  const rendered = await workbook.render({ sheetName, range, scale: 1.25, format: "png" });
  await fs.writeFile(path.join(outputDir, filename), new Uint8Array(await rendered.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(outputDir, "JPM研究更新日历_2026-07-20.xlsx"));

console.log(JSON.stringify({
  workbook: path.join(outputDir, "JPM研究更新日历_2026-07-20.xlsx"),
  scheduleRows: scheduleRows.map((row) => ({
    reportType: row.subscription,
    currentUpdateDate: isoDate(row.latest),
    nextExpectedDate: isoDate(row.predicted),
    frequency: row.frequency,
    confidence: row.confidence,
  })),
}, null, 2));
