import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as echarts from "echarts";
import { ArrowUpDown, CalendarDays, ChevronDown, ChevronRight, Clock3, Loader2, Pause, Play, RefreshCw, Square } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { DragonTigerDetails } from "@/components/ui/DragonTigerDetails";
import { ApiError, api, type DragonTiger, type MarketSnapshot } from "@/lib/api";
import { cn } from "@/lib/utils";

interface MarketView { id: string; label: string; dataset: string; source: string; scopes: string[]; job: string }
interface MarketGroup { id: string; label: string; views: MarketView[] }
const view = (id: string, label: string, dataset: string, source: string, scopes: string[], job: string): MarketView => ({ id, label, dataset, source, scopes, job });

const TECH_VIEWS: MarketView[] = [
  ...["创月新高", "半年新高", "一年新高", "历史新高"].map((p) => view(`cxg-${p}`, p, "tech-ranks", "ths", [`cxg:${p}`], "tech_rank")),
  ...["创月新低", "半年新低", "一年新低", "历史新低"].map((p) => view(`cxd-${p}`, p, "tech-ranks", "ths", [`cxd:${p}`], "tech_rank")),
  ...[5, 10, 20, 30, 60, 90, 250, 500].map((n) => view(`xstp-${n}`, `上破${n}日线`, "tech-ranks", "ths", [`xstp:${n}日均线`], "tech_rank")),
  ...[5, 10, 20, 30, 60, 90, 250, 500].map((n) => view(`xxtp-${n}`, `下破${n}日线`, "tech-ranks", "ths", [`xxtp:${n}日均线`], "tech_rank")),
  ...[["cxfl", "持续放量"], ["cxsl", "持续缩量"], ["ljqd", "量价齐跌"], ["ljqs", "量价齐升"], ["lxsz", "连续上涨"], ["lxxd", "连续下跌"], ["xzjp", "险资举牌"]].map(([key, label]) => view(key, label, "tech-ranks", "ths", [`${key}:`], "tech_rank")),
];

const TECH_GROUPS = [
  ["cxg", "创新高"], ["cxd", "创新低"], ["xstp", "向上突破"], ["xxtp", "向下突破"],
  ["cxfl", "持续放量"], ["cxsl", "持续缩量"], ["ljqd", "量价齐跌"], ["ljqs", "量价齐升"],
  ["lxsz", "连续上涨"], ["lxxd", "连续下跌"], ["xzjp", "险资举牌"],
] as const;
const FUND_SCOPES = [["con", "概念"], ["ind", "行业"]] as const;
const FUND_PERIODS = [["live", "盘中"], ["3", "3日"], ["5", "5日"], ["10", "10日"], ["20", "20日"]] as const;

const GROUPS: MarketGroup[] = [
  { id: "hot", label: "市场热榜", views: [view("hot-em", "东方财富", "hot", "em", [""], "hot"), view("hot-ths", "同花顺", "hot", "ths", [""], "hot"), view("hot-baidu", "百度股市通", "hot", "baidu", [""], "hot")] },
  { id: "fund", label: "资金流向", views: [
    view("fund-ind-live", "行业·盘中", "fund-flow", "ths", ["industry:live", "industry"], "fund_flow"),
    view("fund-con-live", "概念·盘中", "fund-flow", "ths", ["concept:live", "concept"], "fund_flow"),
    ...[3, 5, 10, 20].flatMap((n) => [view(`fund-ind-${n}`, `行业·${n}日`, "fund-flow-daily", "ths", [`industry:${n}`], "fund_flow_daily"), view(`fund-con-${n}`, `概念·${n}日`, "fund-flow-daily", "ths", [`concept:${n}`], "fund_flow_daily")]),
  ] },
  { id: "pool", label: "行情池", views: [view("pool-zt", "涨停池", "pools", "eastmoney", ["zt"], "market_pool"), view("pool-strong", "强势股池", "pools", "eastmoney", ["strong"], "market_pool"), view("pool-zbgc", "炸板池", "pools", "eastmoney", ["zbgc"], "market_pool"), view("pool-dtgc", "跌停池", "pools", "eastmoney", ["dtgc"], "market_pool")] },
  { id: "tech", label: "技术扫描", views: TECH_VIEWS },
  { id: "boards", label: "板块行情", views: [view("board-em", "东方财富概念", "boards", "em", ["live", "daily"], "boards"), view("board-ths", "同花顺概念", "boards", "ths", ["daily"], "boards"), view("board-ind", "行业板块", "boards", "ind", ["daily"], "boards")] },
  { id: "lhb", label: "龙虎榜", views: [view("lhb", "每日上榜", "lhb", "eastmoney", [""], "lhb")] },
];

type Row = Record<string, unknown>;
type Column = { label: string; keys: string[]; format?: "pct" | "plainPct" | "money" | "fund" | "time" | "upDown"; sortable?: boolean };
const COLUMNS: Record<string, Column[]> = {
  hot: [{ label: "排名", keys: ["当前排名", "rank", "order", "排名"] }, { label: "代码", keys: ["代码", "code"] }, { label: "名称", keys: ["股票名称", "name", "名称/代码", "名称"] }, { label: "最新价", keys: ["最新价", "price"] }, { label: "涨跌幅", keys: ["涨跌幅", "rise_and_fall", "change_pct"], format: "pct" }, { label: "热度", keys: ["综合热度", "rate", "heat_value"] }],
  fund: [{ label: "排名", keys: ["rank", "序号"] }, { label: "板块", keys: ["board", "行业"] }, { label: "板块指数", keys: ["board_index", "行业指数"] }, { label: "涨跌幅", keys: ["change_pct", "stage_pct", "行业-涨跌幅", "阶段涨跌幅"], format: "pct" }, { label: "净流入", keys: ["net", "净额"], format: "fund" }, { label: "流入", keys: ["inflow", "流入资金"], format: "fund" }, { label: "流出", keys: ["outflow", "流出资金"], format: "fund" }, { label: "当前价", keys: ["price", "当前价"] }, { label: "领涨股", keys: ["leader", "领涨股"] }, { label: "领涨幅", keys: ["leader_pct", "领涨股-涨跌幅"], format: "pct" }, { label: "公司数", keys: ["company_count", "公司家数"] }],
  lhb: [{ label: "日期", keys: ["上榜日", "trade_date"] }, { label: "代码", keys: ["代码", "code"] }, { label: "名称", keys: ["名称", "name"] }, { label: "上榜原因", keys: ["上榜原因", "reason"] }, { label: "收盘价", keys: ["收盘价"] }, { label: "涨跌幅", keys: ["涨跌幅"], format: "pct" }, { label: "净买额", keys: ["龙虎榜净买额"], format: "money" }, { label: "买入额", keys: ["龙虎榜买入额"], format: "money" }, { label: "卖出额", keys: ["龙虎榜卖出额"], format: "money" }, { label: "龙虎榜成交额", keys: ["龙虎榜成交额"], format: "money" }, { label: "市场总成交额", keys: ["市场总成交额"], format: "money" }, { label: "换手率", keys: ["换手率"], format: "plainPct" }, { label: "解读", keys: ["解读"] }, { label: "上榜后1日", keys: ["上榜后1日"], format: "plainPct" }, { label: "上榜后2日", keys: ["上榜后2日"], format: "plainPct" }, { label: "上榜后5日", keys: ["上榜后5日"], format: "plainPct" }, { label: "上榜后10日", keys: ["上榜后10日"], format: "plainPct" }],
};

const POOL_COMMON: Column[] = [{ label: "代码", keys: ["代码", "code"] }, { label: "名称", keys: ["名称", "name"] }, { label: "涨跌幅", keys: ["涨跌幅"], format: "pct" }, { label: "最新价", keys: ["最新价"] }, { label: "成交额", keys: ["成交额"], format: "money" }, { label: "流通市值", keys: ["流通市值"], format: "money" }, { label: "总市值", keys: ["总市值"], format: "money" }, { label: "换手率", keys: ["换手率"], format: "plainPct" }];
const POOL_EXTRA: Record<string, Column[]> = {
  zt: [{ label: "封板资金", keys: ["封板资金"], format: "money" }, { label: "首次封板", keys: ["首次封板时间"], format: "time" }, { label: "最后封板", keys: ["最后封板时间"], format: "time" }, { label: "炸板次数", keys: ["炸板次数"] }, { label: "涨停统计", keys: ["涨停统计"] }, { label: "连板数", keys: ["连板数"] }],
  strong: [{ label: "涨停价", keys: ["涨停价"] }, { label: "涨速", keys: ["涨速"], format: "pct" }, { label: "是否新高", keys: ["是否新高"] }, { label: "量比", keys: ["量比"] }, { label: "涨停统计", keys: ["涨停统计"] }, { label: "入选理由", keys: ["入选理由"] }],
  zbgc: [{ label: "首次封板", keys: ["首次封板时间"], format: "time" }, { label: "炸板次数", keys: ["炸板次数"] }, { label: "涨停统计", keys: ["涨停统计"] }, { label: "振幅", keys: ["振幅"], format: "plainPct" }],
  dtgc: [{ label: "封单资金", keys: ["封单资金"], format: "money" }, { label: "最后封板", keys: ["最后封板时间"], format: "time" }, { label: "连续跌停", keys: ["连续跌停"] }, { label: "开板次数", keys: ["开板次数"] }],
};

function expandRow(row: Row): Row { if (typeof row.data !== "string") return row; try { return { ...row, ...JSON.parse(row.data) }; } catch { return row; } }
function pick(row: Row, keys: string[]): unknown { for (const key of keys) if (row[key] !== undefined && row[key] !== null && row[key] !== "") return row[key]; return null; }
function numeric(value: unknown): number { return typeof value === "number" ? value : Number(String(value ?? "").replace(/[%,+]/g, "").trim()); }
function sortNumeric(value: unknown): number {
  if (typeof value === "number") return value;
  const raw = String(value ?? "").replace(/[%,+\s]/g, "");
  const multiplier = raw.endsWith("亿") ? 1e8 : raw.endsWith("万") ? 1e4 : 1;
  return Number(raw.replace(/[亿万]/g, "")) * multiplier;
}
function display(value: unknown, format?: Column["format"]): string {
  if (value === null || value === undefined || value === "") return "—";
  if (format === "time") { const raw = String(value).padStart(6, "0"); return /^\d{6}$/.test(raw) ? `${raw.slice(0, 2)}:${raw.slice(2, 4)}:${raw.slice(4)}` : raw; }
  const n = numeric(value);
  if ((format === "pct" || format === "plainPct") && Number.isFinite(n)) return `${format === "pct" && n > 0 ? "+" : ""}${n.toFixed(2)}%`;
  if (format === "fund" && Number.isFinite(n)) return `${n.toFixed(2)} 亿`;
  if (format === "money" && Number.isFinite(n)) { if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)} 亿`; if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)} 万`; }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function codeOf(row: Row): string { return String(pick(row, ["代码", "code", "股票代码"]) || "").match(/\d{6}/)?.[0] || ""; }
function snapshotRows(snapshot: MarketSnapshot): Row[] {
  const payload = Array.isArray(snapshot.payload) ? snapshot.payload : [snapshot.payload];
  return payload.map((row) => expandRow(row as Row));
}

function EChart({ option, className = "h-80" }: { option: echarts.EChartsOption; className?: string }) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!element.current) return;
    chart.current = echarts.init(element.current);
    const observer = new ResizeObserver(() => chart.current?.resize());
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);
  useEffect(() => {
    chart.current?.setOption(option, { lazyUpdate: true, replaceMerge: ["series"] });
  }, [option]);
  return <div ref={element} className={className} />;
}
function MarketSelect({ value, options, onChange, icon, label }: { value: string; options: { value: string; label: string }[]; onChange: (value: string) => void; icon: ReactNode; label: string }) {
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value)?.label || options[0]?.label || "—";
  return <div className="relative">
    <button type="button" aria-label={label} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((current) => !current)}
      className="inline-flex min-w-36 items-center justify-between gap-2 rounded-lg border border-border/60 bg-background/60 px-3 py-2 text-sm text-foreground outline-none transition-colors hover:border-primary/40 focus:border-primary/60">
      <span className="inline-flex items-center gap-2">{icon}{selected}</span><ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
    </button>
    {open && <div role="listbox" className="absolute left-0 z-50 mt-1 max-h-64 min-w-full overflow-y-auto rounded-lg border border-border bg-card p-1 text-sm text-card-foreground shadow-2xl ring-1 ring-black/40">
      {options.map((option) => <button key={option.value || "latest"} type="button" role="option" aria-selected={value === option.value}
        onClick={() => { setOpen(false); onChange(option.value); }}
        className={cn("block w-full whitespace-nowrap rounded-md px-3 py-2 text-left transition-colors hover:bg-muted", value === option.value && "bg-primary/15 text-primary")}>{option.label}</button>)}
    </div>}
  </div>;
}
function columnsFor(groupId: string, current: MarketView, rows: Row[]): Column[] {
  if (groupId === "pool") return [...POOL_COMMON, ...(POOL_EXTRA[current.scopes[0]] || []), { label: "所属行业", keys: ["所属行业"] }].map((column) => ({ ...column, sortable: true }));
  if (groupId === "tech") {
    const ignored = new Set(["id", "data", "created_at", "indicator", "param", "code", "name", "序号"]);
    const keys = rows.length ? Object.keys(rows[0]).filter((key) => !ignored.has(key)) : ["股票代码", "股票简称"];
    const preferred = ["股票代码", "股票简称", ...keys.filter((key) => key !== "股票代码" && key !== "股票简称")];
    return [...new Set(preferred)].map((key) => ({
      label: key,
      keys: [key],
      format: key === "换手率" || key === "累计换手率" ? "plainPct" : /涨跌幅|涨速|振幅|连续涨跌幅/.test(key) ? "pct" : /成交额|流通市值|总市值|持股总数|增持数量/.test(key) ? "money" : undefined,
      sortable: true,
    }));
  }
  if (groupId === "boards") {
    if (current.source === "ths") return [
      { label: "板块", keys: ["board"], sortable: true },
      { label: "排名", keys: ["rank"], sortable: true },
      { label: "涨跌幅", keys: ["change_pct"], format: "pct", sortable: true },
      { label: "涨跌家数", keys: ["up_count", "down_count"], format: "upDown", sortable: true },
      { label: "资金净流入", keys: ["net_inflow"], format: "fund", sortable: true },
      { label: "成交额", keys: ["amount"], format: "fund", sortable: true },
    ];
    return [
      { label: "板块", keys: ["board"], sortable: true },
      { label: "最新价", keys: ["price"], sortable: true },
      { label: "涨跌幅", keys: ["change_pct"], format: "pct", sortable: true },
      { label: "总市值", keys: ["total_mv"], format: "money", sortable: true },
      { label: "换手率", keys: ["turnover"], format: "plainPct", sortable: true },
      { label: "上涨", keys: ["up_count"], sortable: true },
      { label: "下跌", keys: ["down_count"], sortable: true },
      { label: "领涨股", keys: ["leader"], sortable: true },
    ];
  }
  const columns = COLUMNS[groupId];
  return ["fund", "lhb"].includes(groupId) ? columns.map((column) => ({ ...column, sortable: true })) : columns;
}

const CHART_TEXT = "#94a3b8";
const CHART_GRID = "rgba(148,163,184,.16)";
const FUND_LINE_COLORS = ["#60a5fa", "#f97316", "#a78bfa", "#14b8a6", "#ef4444", "#84cc16", "#eab308", "#ec4899", "#22c55e", "#06b6d4", "#f43f5e", "#8b5cf6"];
const FUND_MINUTES = Array.from({ length: 15 * 60 - (9 * 60 + 30) + 1 }, (_, index) => {
  const minutes = 9 * 60 + 30 + index;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
});
function fundBarOption(rows: Row[], field: "net" | "stage_pct", unit: string): echarts.EChartsOption {
  const top = [...rows].sort((left, right) => sortNumeric(pick(right, [field, field === "stage_pct" ? "阶段涨跌幅" : "净额"])) - sortNumeric(pick(left, [field, field === "stage_pct" ? "阶段涨跌幅" : "净额"]))).slice(0, 20).reverse();
  const values = top.map((row) => sortNumeric(pick(row, [field, field === "stage_pct" ? "阶段涨跌幅" : "净额"])));
  return {
    animationDuration: 350,
    grid: { left: 112, right: 58, top: 12, bottom: 30 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value) => `${Number(value).toFixed(2)}${unit}` },
    xAxis: { type: "value", axisLabel: { color: CHART_TEXT }, splitLine: { lineStyle: { color: CHART_GRID } } },
    yAxis: { type: "category", data: top.map((row) => String(pick(row, ["board", "行业"]) || "")), axisLabel: { color: CHART_TEXT, width: 96, overflow: "truncate" } },
    series: [{ type: "bar", data: values.map((value) => ({ value, itemStyle: { color: value >= 0 ? "#ef4444" : "#22c55e" } })), barMaxWidth: 18, label: { show: true, position: "right", color: CHART_TEXT, formatter: ({ value }) => `${Number(value).toFixed(2)}${unit}` } }],
  };
}

export function MarketCenter() {
  const [groupIndex, setGroupIndex] = useState(0);
  const [viewIndex, setViewIndex] = useState(0);
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [expandedRowKeys, setExpandedRowKeys] = useState<Set<string>>(new Set());
  const [detailsByRow, setDetailsByRow] = useState<Record<string, DragonTiger | null>>({});
  const [loadingRowKeys, setLoadingRowKeys] = useState<Set<string>>(new Set());
  const detailEpoch = useRef(0);
  const hotRequest = useRef(0);
  const [hotDates, setHotDates] = useState<string[]>([]);
  const [hotDate, setHotDate] = useState("");
  const [hotBatches, setHotBatches] = useState<MarketSnapshot[]>([]);
  const [hotBatch, setHotBatch] = useState(0);
  const [historyDates, setHistoryDates] = useState<string[]>([]);
  const [historyDate, setHistoryDate] = useState("");
  const [sortLabel, setSortLabel] = useState("");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [selectedBoard, setSelectedBoard] = useState("");
  const [constituents, setConstituents] = useState<Row[]>([]);
  const [constituentsLoading, setConstituentsLoading] = useState(false);
  const boardRequest = useRef(0);
  const [fundHistory, setFundHistory] = useState<MarketSnapshot[]>([]);
  const [fundPlaying, setFundPlaying] = useState(false);
  const [fundPlaybackStarted, setFundPlaybackStarted] = useState(false);
  const [fundPlaybackIndex, setFundPlaybackIndex] = useState(0);
  const group = GROUPS[groupIndex];
  const current = group.views[viewIndex] || group.views[0];
  const techKey = current.id.split("-")[0];
  const techViews = group.id === "tech" ? group.views.map((item, index) => ({ item, index })).filter(({ item }) => item.id === techKey || item.id.startsWith(`${techKey}-`)) : [];
  const fundScope = current.id.includes("-con-") ? "con" : "ind";
  const fundIdParts = current.id.split("-");
  const fundPeriod = current.id.endsWith("-live") ? "live" : fundIdParts[fundIdParts.length - 1] || "live";

  const load = async (dateOverride?: string) => {
    setLoading(true);
    try {
      let found: MarketSnapshot | null = null;
      const date = dateOverride ?? historyDate;
      const scopes = group.id === "boards" && date ? ["daily"] : current.scopes;
      for (const scope of scopes) { found = await api.marketLatest(current.dataset, current.source, scope, ["boards", "tech", "fund", "lhb"].includes(group.id) ? date : ""); if (found) break; }
      setSnapshot(found);
    } catch (error) { toast.error(error instanceof ApiError ? error.message : String(error)); setSnapshot(null); }
    finally { setLoading(false); }
  };

  const loadHot = async (date: string, batch = 0, source = current.source) => {
    const request = ++hotRequest.current;
    setLoading(true);
    setSnapshot(null);
    try {
      const result = await api.marketHistory("hot", source, "", date, 100);
      if (request !== hotRequest.current) return;
      setHotBatches(result.items);
      const selected = result.items.find((item) => item.batch_ts === batch) || result.items[0] || null;
      setHotBatch(selected?.batch_ts || 0);
      setSnapshot(selected);
    } catch (error) {
      if (request === hotRequest.current) toast.error(error instanceof ApiError ? error.message : String(error));
    } finally {
      if (request === hotRequest.current) setLoading(false);
    }
  };

  const loadFundHistory = async (date: string) => {
    if (fundPeriod !== "live") { setFundHistory([]); return; }
    try {
      const candidates = await Promise.all(current.scopes.map((scope) => api.marketHistory(current.dataset, current.source, scope, date, 500)));
      let items = candidates.map((result) => result.items).sort((left, right) => right.length - left.length)[0] || [];
      if (!date && items.length) items = items.filter((item) => item.trade_date === items[0].trade_date);
      setFundHistory([...items].sort((left, right) => left.batch_ts - right.batch_ts));
      setFundPlaying(false); setFundPlaybackStarted(false); setFundPlaybackIndex(0);
    } catch { setFundHistory([]); }
  };

  useEffect(() => {
    if (!fundPlaying || FUND_MINUTES.length < 2) return;
    const timer = window.setInterval(() => setFundPlaybackIndex((index) => {
      if (index >= FUND_MINUTES.length - 1) { setFundPlaying(false); return index; }
      return index + 1;
    }), 120);
    return () => window.clearInterval(timer);
  }, [fundPlaying]);

  useEffect(() => {
    hotRequest.current += 1; setSnapshot(null);
    detailEpoch.current += 1; setExpandedRowKeys(new Set()); setDetailsByRow({}); setLoadingRowKeys(new Set());
    boardRequest.current += 1; setSortLabel(""); setSortDirection("desc"); setSelectedBoard(""); setConstituents([]); setConstituentsLoading(false); setFundHistory([]);
    if (group.id === "fund") {
      setHistoryDates([]); setHistoryDate(""); setLoading(true);
      Promise.all(current.scopes.map((scope) => api.marketDates(current.dataset, current.source, scope))).then((lists) => {
        setHistoryDates([...new Set(lists.flat())].sort().reverse());
        return Promise.all([load(""), loadFundHistory("")]);
      }).catch((error) => { toast.error(error instanceof ApiError ? error.message : String(error)); setLoading(false); });
      return;
    }
    if (group.id === "boards" || group.id === "tech" || group.id === "lhb") {
      setHistoryDates([]); setHistoryDate(""); setLoading(true);
      const scope = group.id === "boards" ? "daily" : current.scopes[0];
      api.marketDates(current.dataset, current.source, scope).then((dates) => {
        setHistoryDates(dates);
        return load("");
      }).catch((error) => { toast.error(error instanceof ApiError ? error.message : String(error)); setLoading(false); });
      return;
    }
    if (group.id !== "hot") { setHistoryDates([]); setHistoryDate(""); void load(""); return; }
    setHotDates([]); setHotDate(""); setHotBatches([]); setHotBatch(0); setLoading(true);
    const request = hotRequest.current;
    const source = current.source;
    api.marketDates("hot", source, "").then((dates) => {
      if (request !== hotRequest.current) return;
      setHotDates(dates);
      const date = dates[0] || "";
      setHotDate(date);
      if (date) return loadHot(date, 0, source);
      setLoading(false);
    }).catch((error) => {
      if (request !== hotRequest.current) return;
      toast.error(error instanceof ApiError ? error.message : String(error)); setLoading(false);
    });
  }, [groupIndex, viewIndex]); // eslint-disable-line react-hooks/exhaustive-deps
  const rows = useMemo(() => {
    if (!snapshot) return [];
    const payload = Array.isArray(snapshot.payload) ? snapshot.payload : [snapshot.payload];
    const expanded = payload.map((row) => expandRow(row as Row));
    return group.id === "fund" ? expanded : expanded.slice(0, group.id === "hot" ? 50 : 200);
  }, [snapshot, group.id]);
  const columns = useMemo(() => columnsFor(group.id, current, rows), [group.id, current, rows]);
  const displayRows = useMemo(() => {
    if (!sortLabel) return rows;
    const column = columns.find((item) => item.label === sortLabel);
    if (!column) return rows;
    const rankValue = (value: unknown) => Number.parseInt(String(value || "0").split("/")[0], 10) || 0;
    return [...rows].sort((left, right) => {
      if (column.format === "upDown") {
        const up = numeric(left.up_count) - numeric(right.up_count);
        const down = numeric(left.down_count) - numeric(right.down_count);
        return (sortDirection === "asc" ? 1 : -1) * (up || down);
      }
      const leftRaw = pick(left, column.keys); const rightRaw = pick(right, column.keys);
      if (leftRaw === null) return 1;
      if (rightRaw === null) return -1;
      const leftNumber = column.keys[0] === "rank" ? rankValue(leftRaw) : sortNumeric(leftRaw);
      const rightNumber = column.keys[0] === "rank" ? rankValue(rightRaw) : sortNumeric(rightRaw);
      const compared = Number.isFinite(leftNumber) && Number.isFinite(rightNumber)
        ? leftNumber - rightNumber
        : String(leftRaw || "").localeCompare(String(rightRaw || ""), "zh-CN");
      return (sortDirection === "asc" ? 1 : -1) * compared;
    });
  }, [rows, columns, sortLabel, sortDirection]);
  const fundLineBoards = useMemo(() => {
    const latest = fundHistory.length ? snapshotRows(fundHistory[fundHistory.length - 1]) : [];
    return [...latest].sort((left, right) => sortNumeric(pick(right, ["net", "净额"])) - sortNumeric(pick(left, ["net", "净额"]))).slice(0, 12).map((row) => String(pick(row, ["board", "行业"]) || ""));
  }, [fundHistory]);
  const fundLineOption = useMemo<echarts.EChartsOption>(() => {
    if (!fundHistory.length) return {};
    const rowsByMinute = new Map<string, Row[]>();
    fundHistory.forEach((item) => rowsByMinute.set(new Date(item.batch_ts * 1000).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }), snapshotRows(item)));
    const visibleIndex = fundPlaybackStarted ? fundPlaybackIndex : FUND_MINUTES.length - 1;
    return {
      animationDuration: 0,
      animationDurationUpdate: 100,
      animationEasingUpdate: "linear",
      color: FUND_LINE_COLORS,
      grid: { left: 68, right: 24, top: 24, bottom: 52 },
      legend: { show: false },
      tooltip: { trigger: "axis", valueFormatter: (value) => `${Number(value).toFixed(2)} 亿` },
      xAxis: { type: "category", boundaryGap: false, data: FUND_MINUTES, axisLabel: { color: CHART_TEXT, hideOverlap: true, formatter: (value: string) => value === "09:30" || value === "15:00" || value.endsWith(":00") ? value : "" }, axisLine: { lineStyle: { color: CHART_GRID } } },
      yAxis: { type: "value", name: "净流入（亿）", nameTextStyle: { color: CHART_TEXT }, axisLabel: { color: CHART_TEXT }, splitLine: { lineStyle: { color: CHART_GRID } } },
      series: fundLineBoards.map((board) => {
        let latestValue: number | null = null;
        const values = FUND_MINUTES.map((minute, index) => {
          const row = rowsByMinute.get(minute)?.find((item) => String(pick(item, ["board", "行业"]) || "") === board);
          const value = pick(row || {}, ["net", "净额"]);
          if (value !== null) latestValue = sortNumeric(value);
          return index <= visibleIndex ? latestValue : null;
        });
        return { id: board, name: board, type: "line", smooth: true, showSymbol: false, connectNulls: true, data: values };
      }),
    };
  }, [fundHistory, fundLineBoards, fundPlaybackIndex, fundPlaybackStarted]);
  const fundPlaybackTime = fundHistory.length ? FUND_MINUTES[fundPlaybackStarted ? fundPlaybackIndex : FUND_MINUTES.length - 1] : "--:--";
  const toggleFundPlayback = () => {
    if (fundPlaying) { setFundPlaying(false); return; }
    if (!fundPlaybackStarted || fundPlaybackIndex >= FUND_MINUTES.length - 1) setFundPlaybackIndex(0);
    setFundPlaybackStarted(true); setFundPlaying(true);
  };
  const stopFundPlayback = () => { setFundPlaying(false); setFundPlaybackStarted(false); setFundPlaybackIndex(0); };
  const fundNetOption = useMemo(() => fundBarOption(rows, "net", " 亿"), [rows]);
  const fundPctOption = useMemo(() => fundBarOption(rows, "stage_pct", "%"), [rows]);

  const run = async () => { try { const result = await api.runCollector(current.job); toast.success(`${result.message}，完成后点“刷新显示”查看`); } catch (error) { toast.error(error instanceof ApiError ? error.message : String(error)); } };
  const backfillDragonTiger = async () => { try { const result = await api.runCollector("lhb_detail"); toast.success(`${result.message}，将补抓最新日榜的个股席位详情`); } catch (error) { toast.error(error instanceof ApiError ? error.message : String(error)); } };
  const sortBy = (column: Column) => {
    if (!column.sortable) return;
    if (sortLabel === column.label) setSortDirection((value) => value === "asc" ? "desc" : "asc");
    else { setSortLabel(column.label); setSortDirection("desc"); }
  };
  const openBoard = async (row: Row) => {
    if (group.id !== "boards" || current.source === "ths") return;
    const board = String(pick(row, ["board", "name"]) || ""); if (!board) return;
    if (selectedBoard === board) { boardRequest.current += 1; setSelectedBoard(""); setConstituents([]); setConstituentsLoading(false); return; }
    const request = ++boardRequest.current;
    setSelectedBoard(board); setConstituents([]); setConstituentsLoading(true);
    try {
      const result = await api.marketLatest("board-constituents", "eastmoney", board);
      const payload = result?.payload;
      if (boardRequest.current === request) setConstituents((Array.isArray(payload) ? payload : []).map((item) => expandRow(item as Row)));
    } catch (error) { if (boardRequest.current === request) toast.error(error instanceof ApiError ? error.message : "板块成分股获取失败"); }
    finally { if (boardRequest.current === request) setConstituentsLoading(false); }
  };
  const openDragonTiger = async (row: Row, rowKey: string) => {
    if (group.id !== "lhb") return;
    const code = codeOf(row); if (!code) return;
    if (expandedRowKeys.has(rowKey)) {
      setExpandedRowKeys((keys) => { const next = new Set(keys); next.delete(rowKey); return next; });
      return;
    }
    setExpandedRowKeys((keys) => new Set(keys).add(rowKey));
    if (detailsByRow[rowKey] || loadingRowKeys.has(rowKey)) return;
    const reason = String(pick(row, ["上榜原因", "reason"]) || "");
    const date = String(pick(row, ["上榜日", "trade_date"]) || snapshot?.trade_date || "").slice(0, 10);
    const epoch = detailEpoch.current;
    setLoadingRowKeys((keys) => new Set(keys).add(rowKey));
    try {
      const result = await api.marketDragonTiger(code, date, reason);
      if (detailEpoch.current === epoch) setDetailsByRow((details) => ({ ...details, [rowKey]: result }));
    } catch (error) {
      if (detailEpoch.current === epoch) toast.error(error instanceof ApiError ? error.message : "龙虎榜详情获取失败");
    } finally {
      if (detailEpoch.current === epoch) setLoadingRowKeys((keys) => { const next = new Set(keys); next.delete(rowKey); return next; });
    }
  };

  return <div>
    <PageHeader title="市场中心" subtitle="全市场热度、资金、行情池、技术形态、板块和龙虎榜" actions={<div className="flex flex-wrap gap-2"><button onClick={run} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><RefreshCw className="h-4 w-4" />立即采集</button>{group.id === "lhb" && <button onClick={backfillDragonTiger} className="rounded-lg border border-primary/50 px-3 py-2 text-sm text-primary">补抓个股详情</button>}</div>} />
    <div className="mb-3 flex gap-2 overflow-x-auto pb-1">{GROUPS.map((item, index) => <button key={item.id} onClick={() => { setGroupIndex(index); setViewIndex(0); }} className={cn("shrink-0 rounded-lg px-3 py-1.5 text-sm", groupIndex === index ? "bg-primary/15 font-medium text-primary shadow-glow" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}</div>
    {group.id === "fund" ? <div className="mb-4 space-y-2 rounded-xl border border-border/60 bg-card/60 p-2.5">
      <div className="flex flex-wrap gap-2">{FUND_SCOPES.map(([key, label]) => <button key={key} onClick={() => { const index = group.views.findIndex((item) => item.id.includes(`-${key}-`) && (fundPeriod === "live" ? item.id.endsWith("-live") : item.id.endsWith(`-${fundPeriod}`))); setViewIndex(index); }} className={cn("rounded-lg border px-3 py-1.5 text-xs transition-colors", fundScope === key ? "border-primary bg-primary/15 font-medium text-primary" : "border-border text-muted-foreground hover:border-primary/60")}>{label}</button>)}</div>
      <div className="flex flex-wrap gap-2 border-t border-border/50 pt-2">{FUND_PERIODS.map(([key, label]) => <button key={key} onClick={() => { const index = group.views.findIndex((item) => item.id.includes(`-${fundScope}-`) && (key === "live" ? item.id.endsWith("-live") : item.id.endsWith(`-${key}`))); setViewIndex(index); }} className={cn("rounded-full px-3 py-1 text-xs", fundPeriod === key ? "bg-primary text-primary-foreground" : "bg-muted/70 text-muted-foreground")}>{label}</button>)}</div>
    </div> : group.id === "tech" ? <div className="mb-4 space-y-2 rounded-xl border border-border/60 bg-card/60 p-2.5">
      <div className="flex flex-wrap gap-2">{TECH_GROUPS.map(([key, label]) => { const index = group.views.findIndex((item) => item.id === key || item.id.startsWith(`${key}-`)); return <button key={key} onClick={() => setViewIndex(index)} className={cn("rounded-lg border px-3 py-1.5 text-xs transition-colors", techKey === key ? "border-primary bg-primary/15 font-medium text-primary" : "border-border text-muted-foreground hover:border-primary/60")}>{label}</button>; })}</div>
      {techViews.length > 1 && <div className="flex flex-wrap gap-2 border-t border-border/50 pt-2">{techViews.map(({ item, index }) => <button key={item.id} onClick={() => setViewIndex(index)} className={cn("rounded-full px-3 py-1 text-xs", viewIndex === index ? "bg-primary text-primary-foreground" : "bg-muted/70 text-muted-foreground")}>{item.label}</button>)}</div>}
    </div> : <div className="mb-4 flex max-h-28 flex-wrap gap-2 overflow-y-auto rounded-xl border border-border/60 bg-card/60 p-2.5">{group.views.map((item, index) => <button key={item.id} onClick={() => setViewIndex(index)} className={cn("rounded-full border px-3 py-1 text-xs transition-colors", viewIndex === index ? "border-primary bg-primary/15 font-medium text-primary" : "border-border text-muted-foreground hover:border-primary/60")}>{item.label}</button>)}</div>}
    {group.id === "hot" && <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-card/60 p-2.5 text-sm">
      <span className="text-xs text-muted-foreground">历史批次</span>
      <MarketSelect label="热榜日期" value={hotDate} options={hotDates.map((date) => ({ value: date, label: date }))} icon={<CalendarDays className="h-3.5 w-3.5 text-muted-foreground" />} onChange={(date) => { setHotDate(date); void loadHot(date); }} />
      <MarketSelect label="热榜时间" value={String(hotBatch)} options={hotBatches.map((item) => ({ value: String(item.batch_ts), label: new Date(item.batch_ts * 1000).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }) }))} icon={<Clock3 className="h-3.5 w-3.5 text-muted-foreground" />} onChange={(value) => { const batch = Number(value); setHotBatch(batch); setSnapshot(hotBatches.find((item) => item.batch_ts === batch) || null); }} />
      <span className="text-xs text-muted-foreground">每半小时一个快照</span>
    </div>}
    {(group.id === "boards" || group.id === "tech" || group.id === "fund" || group.id === "lhb") && <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-card/60 p-2.5 text-sm">
      <span className="text-xs text-muted-foreground">交易日期</span>
      <MarketSelect label="交易日期" value={historyDate} options={[{ value: "", label: "最新数据" }, ...historyDates.map((date) => ({ value: date, label: date }))]} icon={<CalendarDays className="h-3.5 w-3.5 text-muted-foreground" />} onChange={(date) => { detailEpoch.current += 1; setExpandedRowKeys(new Set()); setDetailsByRow({}); setLoadingRowKeys(new Set()); setHistoryDate(date); void load(date); if (group.id === "fund") void loadFundHistory(date); }} />
      <span className="text-xs text-muted-foreground">{group.id === "boards" && !historyDate && current.source === "em" ? "优先显示盘中快照" : group.id === "fund" && fundPeriod === "live" ? "盘中多批次趋势" : group.id === "lhb" ? "选择历史榜单日期" : "选择历史收盘数据"}</span>
    </div>}
    {group.id === "fund" && rows.length > 0 && (fundPeriod === "live" ? <GlassCard className="mb-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">盘中净流入趋势 <span className="font-normal text-muted-foreground">· Top12 板块 · 净额（亿）</span></h3>
        <div className="flex items-center gap-2 text-xs">
          <button type="button" disabled={fundHistory.length < 2} onClick={toggleFundPlayback} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 font-medium text-primary-foreground disabled:opacity-40">{fundPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}{fundPlaying ? "暂停" : fundPlaybackStarted ? "继续" : "播放"}</button>
          <button type="button" disabled={!fundPlaybackStarted} onClick={stopFundPlayback} className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-background/60 px-3 py-1.5 text-foreground disabled:opacity-40"><Square className="h-3 w-3" />停止</button>
          <span className="min-w-12 font-mono text-foreground">{fundPlaybackTime}</span><span className="text-muted-foreground">{fundPlaybackStarted ? Math.min(fundPlaybackIndex + 1, FUND_MINUTES.length) : FUND_MINUTES.length}/{FUND_MINUTES.length}</span>
        </div>
      </div>
      {!!fundLineBoards.length && <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1 border-b border-border/40 pb-2 text-[11px] text-muted-foreground">{fundLineBoards.map((board, index) => <span key={board} className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ backgroundColor: FUND_LINE_COLORS[index % FUND_LINE_COLORS.length] }} />{board}</span>)}</div>}
      {fundHistory.length ? <EChart option={fundLineOption} className="h-[420px]" /> : <p className="py-16 text-center text-sm text-muted-foreground">当前交易日仅有最新排名，暂无多批次趋势数据。</p>}
    </GlassCard> : <div className="mb-4 grid gap-4 xl:grid-cols-2">
      <GlassCard><h3 className="mb-2 text-sm font-semibold">{fundPeriod}日排行 Top20 · 净额</h3><EChart option={fundNetOption} className="h-[520px]" /></GlassCard>
      <GlassCard><h3 className="mb-2 text-sm font-semibold">{fundPeriod}日排行 Top20 · 阶段涨跌幅</h3><EChart option={fundPctOption} className="h-[520px]" /></GlassCard>
    </div>)}
    <GlassCard className="[container-type:inline-size]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2 border-b border-border/50 pb-3"><div><h3 className="font-semibold">{group.label} · {current.label}</h3><p className="text-xs text-muted-foreground">{snapshot ? `${snapshot.trade_date} · ${snapshot.source}${snapshot.scope ? ` · ${snapshot.scope}` : ""} · ${new Date(snapshot.collected_at).toLocaleString("zh-CN")}` : "暂无快照"}</p></div><button onClick={() => group.id === "hot" && hotDate ? void loadHot(hotDate, hotBatch) : void load(historyDate)} disabled={loading} className="inline-flex items-center gap-1.5 text-sm text-primary">{loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{loading ? "读取中…" : "刷新显示"}</button></div>
      {loading && !snapshot ? <p className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取{current.label}数据…</p> : !rows.length ? <p className="py-12 text-center text-sm text-muted-foreground">PostgreSQL 中暂无该分类数据，请执行采集或切换其他分类。</p> : <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead><tr className="border-b border-border text-xs text-muted-foreground">{columns.map((column) => <th key={column.label} className="whitespace-nowrap px-2 py-2 font-medium">{column.sortable ? <button onClick={() => sortBy(column)} className="inline-flex items-center gap-1 hover:text-foreground">{column.label}<ArrowUpDown className={cn("h-3 w-3", sortLabel === column.label && "text-primary")} /></button> : column.label}</th>)}{(group.id === "lhb" || (group.id === "boards" && current.source !== "ths")) && <th className="px-2 py-2 font-medium">详情</th>}</tr></thead><tbody>{displayRows.map((row, index) => {
        const rowReason = String(pick(row, ["上榜原因", "reason"]) || "");
        const rowDate = String(pick(row, ["上榜日", "trade_date"]) || snapshot?.trade_date || "").slice(0, 10);
        const rowKey = `${rowDate}-${codeOf(row)}-${rowReason || index}`;
        const expanded = group.id === "lhb" && expandedRowKeys.has(rowKey);
        const board = String(pick(row, ["board", "name"]) || "");
        const boardExpanded = group.id === "boards" && current.source !== "ths" && selectedBoard === board;
        return <Fragment key={rowKey}>
          <tr onClick={() => group.id === "lhb" ? void openDragonTiger(row, rowKey) : void openBoard(row)} className={cn("border-b border-border/40 hover:bg-muted/30", ((group.id === "lhb" && codeOf(row)) || (group.id === "boards" && current.source !== "ths")) && "cursor-pointer", (expanded || boardExpanded) && "bg-muted/30")}>
            {columns.map((column) => { const value = pick(row, column.keys); const text = column.format === "upDown" ? `${row.up_count ?? 0} / ${row.down_count ?? 0}` : display(value, column.format); const number = numeric(value); return <td key={column.label} title={text} className={cn("max-w-72 truncate whitespace-nowrap px-2 py-2", column.format === "pct" && Number.isFinite(number) && (number > 0 ? "text-danger" : number < 0 ? "text-success" : ""), column.format === "fund" && Number.isFinite(number) && (number > 0 ? "text-danger" : number < 0 ? "text-success" : ""))}>{column.format === "upDown" ? <><span className="text-danger">{String(row.up_count ?? 0)}</span><span className="text-muted-foreground"> / </span><span className="text-success">{String(row.down_count ?? 0)}</span></> : text}</td>; })}
            {group.id === "lhb" && <td className="px-2 py-2 text-primary"><ChevronRight className={cn("h-4 w-4 transition-transform", expanded && "rotate-90")} /></td>}
            {group.id === "boards" && current.source !== "ths" && <td className="px-2 py-2 text-primary"><ChevronRight className={cn("h-4 w-4 transition-transform", boardExpanded && "rotate-90")} /></td>}
          </tr>
          {expanded && <tr className="border-b border-border/60 bg-muted/15"><td colSpan={columns.length + 1} className="px-2 py-3 sm:px-5 sm:py-4">
            <div className="sticky left-2 w-[calc(100cqw-1rem)] max-w-none sm:left-5 sm:w-[calc(100cqw-2.5rem)]">{loadingRowKeys.has(rowKey) ? <p className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取龙虎榜席位详情…</p> : detailsByRow[rowKey] && (detailsByRow[rowKey]!.seats.buy.length || detailsByRow[rowKey]!.seats.sell.length) ? <DragonTigerDetails data={detailsByRow[rowKey]!} title={`龙虎榜 · ${pick(row, ["名称", "name"]) || codeOf(row)} ${codeOf(row)}${rowReason ? ` · ${rowReason}` : ""}`} periodLabel="当日" seatsOnly /> : <p className="py-8 text-center text-sm text-muted-foreground">该股在当前榜单日期和上榜原因下暂无席位详情。</p>}</div>
          </td></tr>}
          {boardExpanded && <tr className="border-b border-border/60 bg-muted/15"><td colSpan={columns.length + 1} className="px-2 py-3 sm:px-5 sm:py-4">
            <div className="sticky left-2 w-[calc(100cqw-1rem)] max-w-none sm:left-5 sm:w-[calc(100cqw-2.5rem)]">
              <h4 className="mb-3 text-sm font-semibold">{board} · 成分股（{constituents.length}）</h4>
              {constituentsLoading ? <p className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取成分股…</p> : !constituents.length ? <p className="py-8 text-center text-sm text-muted-foreground">暂无已落库的成分股数据。</p> : <div className="max-h-96 overflow-y-auto rounded-lg border border-border/60"><table className="w-full min-w-[620px] text-sm"><thead className="sticky top-0 bg-card"><tr className="border-b border-border text-xs text-muted-foreground"><th className="px-2 py-2 text-left">代码</th><th className="px-2 py-2 text-left">名称</th><th className="px-2 py-2 text-right">最新价</th><th className="px-2 py-2 text-right">涨跌幅</th><th className="px-2 py-2 text-right">换手率</th><th className="px-2 py-2 text-right">成交额</th></tr></thead><tbody>{constituents.map((stock, stockIndex) => { const pct = numeric(stock.change_pct); return <tr key={`${stock.code}-${stockIndex}`} className="border-b border-border/40"><td className="px-2 py-2 font-mono">{String(stock.code || "—")}</td><td className="px-2 py-2 font-medium">{String(stock.name || "—")}</td><td className="px-2 py-2 text-right">{display(stock.price)}</td><td className={cn("px-2 py-2 text-right", Number.isFinite(pct) && (pct > 0 ? "text-danger" : pct < 0 ? "text-success" : ""))}>{display(stock.change_pct, "pct")}</td><td className="px-2 py-2 text-right">{display(stock.turnover, "plainPct")}</td><td className="px-2 py-2 text-right">{display(stock.amount, "money")}</td></tr>; })}</tbody></table></div>}
            </div>
          </td></tr>}
        </Fragment>;
      })}</tbody></table>{group.id !== "fund" && Array.isArray(snapshot?.payload) && snapshot.payload.length > 200 && <p className="mt-3 text-xs text-muted-foreground">当前展示前 200 条，共 {snapshot.payload.length} 条。</p>}</div>}
    </GlassCard>
    <Disclaimer />
  </div>;
}
