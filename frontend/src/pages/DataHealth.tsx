import { useCallback, useEffect, useMemo, useState } from "react";
import { Database, Loader2, Play, RefreshCw, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, ApiError, type DataHealth as HealthData } from "@/lib/api";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  quote: "行情", valuation: "估值", reports: "研报", percentile: "估值分位",
  financials: "财务", announcements: "公告", news: "新闻", margin: "融资融券",
  block_trade: "大宗交易", holders: "股东户数", dividend: "分红", fund_flow: "资金流",
  dragon_tiger: "龙虎榜", lockup: "限售解禁", blocks: "板块归属",
  hot_concepts: "热门概念", investor_qa: "互动易",
};

const timeText = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—";

export function DataHealth() {
  const [data, setData] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState("");
  const load = useCallback(async () => {
    try { setData(await api.dataHealth()); }
    catch (error) { toast.error(error instanceof ApiError ? error.message : String(error)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (data?.stock_run?.status !== "running") return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [data?.stock_run?.status, load]);
  const run = async (job: string) => {
    setRunning(job);
    try { const result = await api.runCollector(job); toast.success(result.message); window.setTimeout(() => void load(), 1200); }
    catch (error) { toast.error(error instanceof ApiError ? error.message : String(error)); }
    finally { setRunning(""); }
  };
  const progress = data?.stock_run && data.stock_run.total_tasks
    ? Math.min(100, data.stock_run.completed_tasks / data.stock_run.total_tasks * 100) : 0;
  const details = data?.stock_run?.detail || {};
  const eta = Number(details.eta_seconds || 0);
  const etaText = eta ? `${Math.floor(eta / 3600)}小时${Math.floor(eta % 3600 / 60)}分` : "—";
  const failing = useMemo(() => data?.stock_coverage.filter((item) => item.failed || item.missing) || [], [data]);

  return <div>
    <PageHeader title="数据健康" subtitle="PostgreSQL 覆盖率、采集任务与缺失数据" actions={<button onClick={() => { setLoading(true); void load(); }} className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新</button>} />
    {loading && !data ? <p className="flex items-center justify-center gap-2 py-20 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取数据库状态…</p> : data && <>
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <GlassCard><p className="text-xs text-muted-foreground">数据库版本</p><p className="mt-1 text-2xl font-bold">schema v{data.schema_version}</p></GlassCard>
        <GlassCard><p className="text-xs text-muted-foreground">A 股股票主表</p><p className="mt-1 text-2xl font-bold">{data.stock_universe.toLocaleString()} 只</p></GlassCard>
        <GlassCard><p className="text-xs text-muted-foreground">检查交易日</p><p className="mt-1 text-2xl font-bold">{data.trade_date}</p></GlassCard>
      </div>

      <GlassCard className="mb-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-semibold">全量个股归档</h3><p className="text-xs text-muted-foreground">最近运行：{data.stock_run?.status || "尚未运行"} · {timeText(data.stock_run?.started_at)}</p></div><div className="flex gap-2"><button disabled={!!running} onClick={() => void run("stock_archive")} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground"><Play className="h-3.5 w-3.5" />全量采集</button><button disabled={!!running} onClick={() => void run("stock_archive_retry")} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs"><RotateCcw className="h-3.5 w-3.5" />补采失败项</button></div></div>
        <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${progress}%` }} /></div>
        <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground"><span>{data.stock_run?.completed_tasks.toLocaleString() || 0} / {data.stock_run?.total_tasks.toLocaleString() || 0} 项 · 失败 {data.stock_run?.failed_tasks.toLocaleString() || 0}</span><span>股票 {String(details.stocks_done || 0)} / {data.stock_run?.total_stocks || 0} · 预计剩余 {etaText}</span></div>
      </GlassCard>

      {data.stock_failures.length > 0 && <GlassCard className="mb-4"><h3 className="mb-3 font-semibold">失败项明细 <span className="text-xs font-normal text-muted-foreground">· 最近 {data.stock_failures.length} 项</span></h3><div className="max-h-72 overflow-auto"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-card text-muted-foreground"><tr><th className="p-2">股票</th><th className="p-2">数据类型</th><th className="p-2">错误</th><th className="p-2">更新时间</th></tr></thead><tbody>{data.stock_failures.map((item) => <tr key={`${item.code}-${item.data_type}`} className="border-t border-border/40"><td className="p-2 font-mono">{item.code}</td><td className="p-2">{LABELS[item.data_type] || item.data_type}</td><td className="max-w-xl p-2 text-danger" title={item.error}>{item.error}</td><td className="p-2">{timeText(item.updated_at)}</td></tr>)}</tbody></table></div></GlassCard>}

      <GlassCard className="mb-4">
        <h3 className="mb-3 font-semibold">个股数据覆盖率 <span className="text-xs font-normal text-muted-foreground">· {failing.length ? `${failing.length} 类未完整` : "全部完整"}</span></h3>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{data.stock_coverage.map((item) => <div key={item.data_type} className="rounded-lg border border-border/50 p-3"><div className="flex justify-between text-sm"><span>{LABELS[item.data_type] || item.data_type}</span><b className={item.coverage_pct === 100 ? "text-success" : item.failed ? "text-danger" : "text-warning"}>{item.coverage_pct}%</b></div><div className="mt-2 h-1.5 overflow-hidden rounded bg-muted"><div className="h-full bg-primary" style={{ width: `${item.coverage_pct}%` }} /></div><p className="mt-1.5 text-[11px] text-muted-foreground">成功 {item.success} · 失败 {item.failed} · 待采 {item.missing}</p></div>)}</div>
      </GlassCard>

      <GlassCard className="mb-4"><h3 className="mb-3 flex items-center gap-2 font-semibold"><Database className="h-4 w-4 text-primary" />市场数据集</h3><div className="max-h-96 overflow-auto"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-card text-muted-foreground"><tr><th className="p-2">数据集</th><th className="p-2">来源</th><th className="p-2">快照</th><th className="p-2">覆盖日期</th><th className="p-2">最近采集</th></tr></thead><tbody>{data.datasets.map((item) => <tr key={`${item.dataset}-${item.source}`} className="border-t border-border/40"><td className="p-2">{item.dataset}</td><td className="p-2">{item.source}</td><td className="p-2 font-mono">{item.snapshots}</td><td className="p-2">{item.first_date} ～ {item.last_date}</td><td className="p-2">{timeText(item.latest_collect)}</td></tr>)}</tbody></table></div></GlassCard>
      <GlassCard><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">最近采集任务</h3><div className="flex gap-2"><button onClick={() => void run("trade_calendar")} className="rounded-lg border border-border px-2.5 py-1 text-xs">同步交易日历</button><button onClick={() => void run("stocks")} className="rounded-lg border border-border px-2.5 py-1 text-xs">同步股票列表</button></div></div><div className="max-h-80 overflow-auto"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-card text-muted-foreground"><tr><th className="p-2">任务</th><th className="p-2">来源</th><th className="p-2">状态</th><th className="p-2">说明</th><th className="p-2">时间</th></tr></thead><tbody>{data.jobs.map((item) => <tr key={item.job} className="border-t border-border/40"><td className="p-2">{item.job}</td><td className="p-2">{item.source || "—"}</td><td className={cn("p-2", item.status === "ok" ? "text-success" : item.status === "fail" ? "text-danger" : "text-warning")}>{item.status}</td><td className="max-w-96 truncate p-2" title={item.detail}>{item.detail || "—"}</td><td className="p-2">{timeText(item.finished_at || item.started_at)}</td></tr>)}</tbody></table></div></GlassCard>
    </>}
  </div>;
}
