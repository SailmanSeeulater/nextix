import { describe, expect, it } from "vitest";
import {
  MIN_BAR,
  axisLabelIndexes,
  chartScale,
  chartSummary,
  costsHref,
  dayRange,
  formatAxisUsd,
  formatDay,
  formatTokens,
  formatUsd,
  niceCeiling,
  parseDays,
  readCostReport,
  runsWord,
} from "./costs";
import type { CostDay } from "./types";

function day(date: string, cost_usd = 0, runs = 0): CostDay {
  return { date, cost_usd, runs, input_tokens: 0, output_tokens: 0 };
}

describe("parseDays", () => {
  it("accepts the offered windows", () => {
    expect(parseDays("7")).toBe(7);
    expect(parseDays("30")).toBe(30);
    expect(parseDays("90")).toBe(90);
    expect(parseDays(["90", "7"])).toBe(90);
  });

  it("falls back to 30 days for anything else", () => {
    for (const raw of [undefined, null, "", "0", "14", "abc", "7.5", "-7", "365"]) {
      expect(parseDays(raw)).toBe(30);
    }
  });

  it("keeps the default window's URL bare", () => {
    expect(costsHref(30)).toBe("/costs");
    expect(costsHref(7)).toBe("/costs?days=7");
  });
});

describe("readCostReport", () => {
  it("turns Decimal strings into numbers and keeps the shape", () => {
    const report = readCostReport({
      days: 7,
      estimated: true,
      total: { cost_usd: "1.2500", input_tokens: 1000, output_tokens: "200", runs: 3 },
      by_day: [{ date: "2026-09-20", cost_usd: "1.25", input_tokens: 1000, output_tokens: 200, runs: 3 }],
      by_repo: [{ repo: "acme/web", cost_usd: 1.25, input_tokens: 1000, output_tokens: 200, runs: 3 }],
      by_ticket: [
        {
          ticket_id: "t1",
          repo: "acme/web",
          issue_number: 12,
          title: "Fix it",
          cost_usd: 1.25,
          input_tokens: 1000,
          output_tokens: 200,
          runs: 3,
        },
      ],
    });
    expect(report?.estimated).toBe(true);
    expect(report?.total).toEqual({ cost_usd: 1.25, input_tokens: 1000, output_tokens: 200, runs: 3 });
    expect(report?.by_day[0]).toMatchObject({ date: "2026-09-20", cost_usd: 1.25 });
    expect(report?.by_ticket[0]).toMatchObject({ ticket_id: "t1", issue_number: 12, title: "Fix it" });
  });

  it("drops unreadable rows and fills missing numbers with zero", () => {
    const report = readCostReport({
      total: {},
      by_day: [{ date: "yesterday" }, null, { date: "2026-09-21", cost_usd: "NaN" }],
      by_repo: [{ cost_usd: 1 }],
      by_ticket: [{ ticket_id: "t2", title: "" }],
    });
    expect(report?.estimated).toBe(false);
    expect(report?.total).toEqual({ cost_usd: 0, input_tokens: 0, output_tokens: 0, runs: 0 });
    expect(report?.by_day).toEqual([day("2026-09-21")]);
    expect(report?.by_repo).toEqual([]);
    expect(report?.by_ticket[0]).toMatchObject({ title: "Untitled ticket", repo: "", issue_number: 0 });
  });

  it("refuses a body that isn't a report", () => {
    expect(readCostReport(null)).toBeNull();
    expect(readCostReport("nope")).toBeNull();
    expect(readCostReport({ total: {} })).toBeNull();
    expect(readCostReport({ by_day: [] })).toBeNull();
  });
});

describe("formatting", () => {
  it("writes dollars with cents and flags tiny non-zero costs", () => {
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(0.004)).toBe("<$0.01");
    expect(formatUsd(0.005)).toBe("$0.01");
    expect(formatUsd(1204.5)).toBe("$1,204.50");
  });

  it("writes axis values short", () => {
    expect(formatAxisUsd(0)).toBe("$0");
    expect(formatAxisUsd(5)).toBe("$5");
    expect(formatAxisUsd(2.5)).toBe("$2.50");
    expect(formatAxisUsd(0.25)).toBe("$0.25");
    expect(formatAxisUsd(0.05)).toBe("$0.05");
    expect(formatAxisUsd(0.025)).toBe("$0.025");
    expect(formatAxisUsd(1500)).toBe("$1.5k");
    expect(formatAxisUsd(2000)).toBe("$2k");
  });

  it("writes token counts compactly", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(812)).toBe("812");
    expect(formatTokens(1000)).toBe("1k");
    expect(formatTokens(1250)).toBe("1.3k");
    expect(formatTokens(12_400)).toBe("12k");
    expect(formatTokens(999_499)).toBe("999k");
    expect(formatTokens(999_600)).toBe("1M");
    expect(formatTokens(3_140_000)).toBe("3.1M");
  });

  it("writes UTC days without a time zone shifting them", () => {
    expect(formatDay("2026-09-03")).toBe("Sep 3");
    expect(formatDay("2026-01-31", true)).toBe("Jan 31, 2026");
    expect(formatDay("not-a-day")).toBe("not-a-day");
  });

  it("writes a span of days", () => {
    expect(dayRange([])).toBe("");
    expect(dayRange([day("2026-09-26")])).toBe("Sep 26, 2026");
    expect(dayRange([day("2026-08-28"), day("2026-09-26")])).toBe("Aug 28 – Sep 26");
    expect(dayRange([day("2025-12-28"), day("2026-01-03")])).toBe("Dec 28, 2025 – Jan 3, 2026");
  });

  it("counts runs in words", () => {
    expect(runsWord(1)).toBe("1 run");
    expect(runsWord(0)).toBe("0 runs");
    expect(runsWord(1200)).toBe("1,200 runs");
  });
});

describe("chart scaling", () => {
  it("rounds the axis up to a friendly number", () => {
    expect(niceCeiling(0)).toBe(0);
    expect(niceCeiling(-3)).toBe(0);
    expect(niceCeiling(Number.NaN)).toBe(0);
    expect(niceCeiling(1)).toBe(1);
    expect(niceCeiling(1.2)).toBe(2);
    expect(niceCeiling(2.1)).toBe(2.5);
    expect(niceCeiling(3)).toBe(5);
    expect(niceCeiling(7)).toBe(10);
    expect(niceCeiling(0.3)).toBe(0.5);
    expect(niceCeiling(0.021)).toBe(0.025);
    expect(niceCeiling(420)).toBe(500);
  });

  it("scales bars against the axis top", () => {
    const { max, heights } = chartScale([0, 1, 4, 2]);
    expect(max).toBe(5);
    expect(heights).toEqual([0, 0.2, 0.8, 0.4]);
  });

  it("keeps a tiny day visible", () => {
    const { heights } = chartScale([100, 0.01, 0]);
    expect(heights[1]).toBe(MIN_BAR);
    expect(heights[2]).toBe(0);
  });

  it("handles all-zero and empty data", () => {
    expect(chartScale([0, 0, 0])).toEqual({ max: 0, heights: [0, 0, 0] });
    expect(chartScale([])).toEqual({ max: 0, heights: [] });
  });

  it("picks dates to label under the chart", () => {
    expect(axisLabelIndexes(0)).toEqual([]);
    expect(axisLabelIndexes(1)).toEqual([0]);
    expect(axisLabelIndexes(3)).toEqual([0, 2]);
    expect(axisLabelIndexes(8)).toEqual([0, 3, 7]);
    expect(axisLabelIndexes(31)).toEqual([0, 15, 30]);
  });

  it("describes the chart in one sentence", () => {
    expect(chartSummary([])).toBe("No days to show.");
    expect(chartSummary([day("2026-09-01"), day("2026-09-02")])).toBe(
      "No cost was recorded on any day. 0 of 2 days had runs.",
    );
    expect(
      chartSummary([day("2026-09-01", 0.5, 1), day("2026-09-02", 2, 3), day("2026-09-03")]),
    ).toBe("Highest: $2.00 on Sep 2. 2 of 3 days had runs.");
  });
});
