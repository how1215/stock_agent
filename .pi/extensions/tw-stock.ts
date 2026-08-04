/**
 * 台股數據查詢工具 — Pi extension。
 *
 * Pi 不支援 MCP，整合外部能力只能走 CLI 或 extension。這裡走 extension，
 * 把 Python 側的 JSON 子命令包成 typed tool，讓 Pi 的模型直接讀數據自己解讀。
 *
 * 刻意不在這裡格式化數字：Python 回傳的 payload 已經帶一個 `text` 欄位（由
 * stock_agent/render.py 渲染），與 Python agent 看到的文字完全相同。若在
 * TypeScript 裡重寫一遍格式化，兩邊遲早會漂移。
 *
 * 用 node:child_process 而非 pi.exec，因為 execFile 的行為（非零退出時
 * 拋出且帶 stdout/stderr）是明確的，錯誤處理寫得起來。
 */

import { execFile } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const execFileAsync = promisify(execFile);

// .pi/extensions/tw-stock.ts → 往上兩層是專案根目錄，與 cwd 無關
const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

type Payload = { text: string; trade_date: string } & Record<string, unknown>;

function parseJson(raw: string | undefined): unknown {
  if (!raw?.trim()) return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

/**
 * 跑一個 CLI 子命令並回傳解析後的 payload。
 *
 * CLI 契約：stdout 只有 JSON、日誌走 stderr、失敗時 exit 非 0 且 stdout 給
 * {"error": "..."}。所以錯誤訊息要從失敗結果的 stdout 撈出來。
 */
async function runCli(args: string[], signal?: AbortSignal): Promise<Payload> {
  let stdout: string;
  try {
    ({ stdout } = await execFileAsync(
      "uv",
      ["run", "python", "-m", "stock_agent.cli", ...args],
      { cwd: PROJECT_ROOT, signal, maxBuffer: 16 * 1024 * 1024 },
    ));
  } catch (err) {
    const failure = err as { stdout?: string; stderr?: string; message?: string };
    const payload = parseJson(failure.stdout);
    if (payload && typeof payload === "object" && "error" in payload) {
      throw new Error(String((payload as { error: unknown }).error));
    }
    throw new Error(
      failure.stderr?.trim() || failure.message || "stock_agent CLI 執行失敗",
    );
  }

  const payload = parseJson(stdout);
  if (!payload || typeof payload !== "object") {
    throw new Error(`stock_agent CLI 回傳的不是 JSON：${stdout.slice(0, 200)}`);
  }
  return payload as Payload;
}

function toResult(payload: Payload) {
  return {
    content: [{ type: "text" as const, text: payload.text }],
    details: payload,
  };
}

/** 只在有值時附上旗標，避免把 undefined 傳成字串 "undefined"。 */
function flag(name: string, value: string | number | undefined): string[] {
  return value === undefined || value === null ? [] : [name, String(value)];
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "stock_market",
    label: "台股大盤",
    description:
      "查詢台股大盤（發行量加權股價指數）的收盤點數與漲跌。用於「大盤怎麼樣」、「今天盤勢如何」這類問題。",
    promptSnippet: "查台股大盤加權指數收盤",
    parameters: Type.Object({
      date: Type.Optional(
        Type.String({ description: "交易日 YYYY-MM-DD，省略取最新交易日（建議省略）" }),
      ),
    }),
    async execute(_id, params, signal) {
      return toResult(await runCli(["market", ...flag("--date", params.date)], signal));
    },
  });

  pi.registerTool({
    name: "stock_quote",
    label: "台股個股",
    description:
      "查詢指定台股上市個股的當日開高低收、漲跌幅與成交量。可傳股票代號或公司名稱。",
    promptSnippet: "查台股個股收盤數據",
    promptGuidelines: [
      "不確定股票代號時直接傳公司名稱（如「台積電」），不要憑記憶猜代號。",
      "要查多檔時一次把代號全放進 codes，不要一檔呼叫一次。",
    ],
    parameters: Type.Object({
      codes: Type.Array(Type.String(), {
        description: '股票代號或公司名稱，例如 ["2330", "鴻海"]',
        minItems: 1,
      }),
      date: Type.Optional(
        Type.String({ description: "交易日 YYYY-MM-DD，省略取最新交易日（建議省略）" }),
      ),
    }),
    async execute(_id, params, signal) {
      return toResult(
        await runCli(["quote", ...params.codes, ...flag("--date", params.date)], signal),
      );
    },
  });

  pi.registerTool({
    name: "stock_screen",
    label: "台股篩選",
    description:
      "依條件篩選並排序全市場台股上市個股。用於「今天漲最多的前 N 檔」、「成交量最大的股票」這類需要掃過整個市場的問題。",
    promptSnippet: "篩選排序全市場台股個股",
    promptGuidelines: [
      "查特定幾檔請用 stock_quote，不要用這個工具再自己過濾。",
      "問跌最多時用 sortBy=change_pct 加 order=asc。",
    ],
    parameters: Type.Object({
      sortBy: StringEnum(["change_pct", "volume", "turnover", "close"] as const, {
        description: "排序欄位：漲跌幅 / 成交股數 / 成交金額 / 收盤價",
      }),
      order: StringEnum(["desc", "asc"] as const, {
        description: "desc 由大到小（漲最多、量最大）；asc 由小到大（跌最多）",
      }),
      limit: Type.Integer({ minimum: 1, maximum: 100, description: "回傳檔數" }),
      minChangePct: Type.Optional(
        Type.Number({ description: "只留漲跌幅 ≥ 此值的個股，例如 5 表示漲超過 5%" }),
      ),
      minVolume: Type.Optional(
        Type.Integer({ minimum: 0, description: "只留成交股數 ≥ 此值的個股" }),
      ),
      includeEtf: Type.Optional(
        Type.Boolean({ description: "納入 ETF 與受益證券，預設只看普通股" }),
      ),
      date: Type.Optional(
        Type.String({ description: "交易日 YYYY-MM-DD，省略取最新交易日（建議省略）" }),
      ),
    }),
    async execute(_id, params, signal) {
      const args = [
        "screen",
        "--sort-by",
        params.sortBy,
        "--order",
        params.order,
        "--limit",
        String(params.limit),
        ...flag("--min-change-pct", params.minChangePct),
        ...flag("--min-volume", params.minVolume),
        ...(params.includeEtf ? ["--include-etf"] : []),
        ...flag("--date", params.date),
      ];
      return toResult(await runCli(args, signal));
    },
  });

  pi.registerTool({
    name: "stock_scan",
    label: "台股逐檔判讀",
    description:
      "對候選台股個股逐檔判讀，找出符合「說得出來但寫不成數字條件」的股票，例如「收盤收在最高、買盤強勢」、「開高走低、賣壓明顯」。每檔一次模型判讀，很慢（15 檔約 45 秒）。",
    promptSnippet: "逐檔判讀台股，找符合描述性特徵的股票（慢）",
    promptGuidelines: [
      "純數值條件（漲幅前 N 名、成交量最大）一律用 stock_screen，不要用這個。",
      "查特定幾檔用 stock_quote。",
      "這個工具耗時數十秒，只在描述性條件真的無法用數值篩選表達時才用。",
    ],
    parameters: Type.Object({
      focus: Type.String({
        description: "要找的特徵，自然語言描述，越具體越好",
        minLength: 1,
      }),
      sortBy: StringEnum(["turnover", "volume", "change_pct", "close"] as const, {
        description: "先用哪個欄位挑候選；turnover 取市場焦點股",
      }),
      order: StringEnum(["desc", "asc"] as const),
      limit: Type.Integer({
        minimum: 1,
        maximum: 30,
        description: "掃描幾檔；越大越慢，建議 10–20",
      }),
      minChangePct: Type.Optional(Type.Number({ description: "候選只留漲跌幅 ≥ 此值" })),
      minVolume: Type.Optional(Type.Integer({ minimum: 0, description: "候選只留成交股數 ≥ 此值" })),
      date: Type.Optional(Type.String({ description: "交易日 YYYY-MM-DD，省略取最新" })),
    }),
    async execute(_id, params, signal, onUpdate) {
      // 這個工具動輒數十秒，先回報進度免得看起來像卡住
      onUpdate?.({
        content: [{
          type: "text",
          text: `正在逐檔判讀 ${params.limit} 檔（每檔一次模型呼叫，預計數十秒）…`,
        }],
      });

      const args = [
        "scan",
        params.focus,
        "--json",
        "--sort-by",
        params.sortBy,
        "--order",
        params.order,
        "--limit",
        String(params.limit),
        ...flag("--min-change-pct", params.minChangePct),
        ...flag("--min-volume", params.minVolume),
        ...flag("--date", params.date),
      ];
      return toResult(await runCli(args, signal));
    },
  });

  pi.registerTool({
    name: "stock_briefing",
    label: "台股盤前提要",
    description:
      "讀取今天的台股盤前提要：大盤、市場寬度（漲跌家數）、自選股逐檔判讀、市場焦點。報告通常已由每日排程生成好，讀取是即時的。",
    promptSnippet: "讀今天的台股盤前提要",
    promptGuidelines: [
      "「今天要注意什麼」、「盤前看一下」這類問題用這個工具。",
      "refresh 要跑約 80 秒，只在使用者明確要求重新產生時才設 true。",
      "報告日與依據的交易日不同（盤前報告依據前一交易日），轉述時要照報告內文的日期講。",
    ],
    parameters: Type.Object({
      refresh: Type.Optional(
        Type.Boolean({ description: "強制重新生成，約需 80 秒。預設讀既有報告" }),
      ),
      reportDate: Type.Optional(
        Type.String({ description: "報告日 YYYY-MM-DD，預設今天（台北時區）" }),
      ),
    }),
    async execute(_id, params, signal, onUpdate) {
      if (params.refresh) {
        onUpdate?.({
          content: [{ type: "text", text: "正在重新生成盤前提要（約需 80 秒）…" }],
        });
      }
      const args = [
        "briefing",
        "--json",
        ...(params.refresh ? ["--refresh"] : []),
        ...flag("--report-date", params.reportDate),
      ];
      return toResult(await runCli(args, signal));
    },
  });

  pi.registerTool({
    name: "stock_watchlist",
    label: "台股自選股",
    description:
      "查看或修改使用者的自選股清單。清單會影響每日盤前提要裡逐檔判讀的範圍。",
    promptSnippet: "查看或修改台股自選股清單",
    promptGuidelines: [
      "「我的自選股」、「我關注的股票」用 action=list，不要用 stock_screen 去猜。",
      "add 與 remove 接受股票代號或公司名稱。",
      "沒有清空整份清單的功能——使用者要清空時請他用 CLI 的 `watchlist clear`。",
    ],
    parameters: Type.Object({
      action: StringEnum(["list", "add", "remove"] as const, {
        description: "list 查看；add 加入；remove 移除",
      }),
      codes: Type.Optional(
        Type.Array(Type.String(), {
          description: 'add / remove 的對象，代號或公司名稱，例如 ["2330", "鴻海"]',
        }),
      ),
    }),
    async execute(_id, params, signal) {
      if (params.action !== "list" && !params.codes?.length) {
        throw new Error(`${params.action} 需要至少一個股票代號或公司名稱`);
      }
      const args = ["watchlist", params.action, ...(params.codes ?? []), "--json"];
      return toResult(await runCli(args, signal));
    },
  });
}
