import { Trophy } from "lucide-react";
import { type DragonTiger } from "@/lib/api";
import { cn } from "@/lib/utils";

export function DragonTigerDetails({ data, title = "龙虎榜", periodLabel = "近30日", seatsOnly = false }: { data: DragonTiger; title?: string; periodLabel?: string; seatsOnly?: boolean }) {
  return <div className="min-w-0 overflow-hidden">
    <h3 className="mb-3 flex min-w-0 items-start gap-1.5 break-words text-sm font-semibold">
      <Trophy className="h-4 w-4 shrink-0 text-primary" /> <span className="min-w-0">{title}{!seatsOnly && `（${periodLabel} ${data.records.length} 次）`}</span>
    </h3>
    {!seatsOnly && <div className="space-y-2">
      {data.records.slice(0, 6).map((row, index) => <div key={index} className="min-w-0 border-b border-border/40 pb-2 text-sm last:border-0">
        <div className="mb-1 flex min-w-0 flex-wrap justify-between gap-1">
          <span className="font-mono text-xs text-muted-foreground">{row.date}</span>
          <span className={cn("font-mono text-xs", row.net_buy >= 0 ? "text-danger" : "text-success")}>净买 {row.net_buy} 万</span>
        </div>
        <p className="min-w-0 break-words">{row.reason}</p>
      </div>)}
    </div>}
    {(data.seats.buy.length > 0 || data.seats.sell.length > 0) && <div className="mt-3 grid min-w-0 gap-4 border-t border-border/40 pt-3 lg:grid-cols-2">
      <div>
        <p className="mb-1.5 text-xs font-medium text-danger">买入席位 TOP</p>
        {data.seats.buy.map((seat, index) => <div key={index} className="flex min-w-0 flex-wrap justify-between gap-x-2 text-xs text-muted-foreground"><span className="min-w-0 flex-1 basis-48 break-words">{seat.name}</span><span className="font-mono">净{seat.net}万</span></div>)}
      </div>
      <div>
        <p className="mb-1.5 text-xs font-medium text-success">卖出席位 TOP</p>
        {data.seats.sell.map((seat, index) => <div key={index} className="flex min-w-0 flex-wrap justify-between gap-x-2 text-xs text-muted-foreground"><span className="min-w-0 flex-1 basis-48 break-words">{seat.name}</span><span className="font-mono">净{seat.net}万</span></div>)}
      </div>
    </div>}
    {!seatsOnly && (data.institution.buy_amt !== 0 || data.institution.sell_amt !== 0) && <p className="mt-3 break-words border-t border-border/40 pt-2 text-xs text-muted-foreground">
      机构席位：买入 {data.institution.buy_amt} 万 · 卖出 {data.institution.sell_amt} 万 · 净额 <span className={data.institution.net_amt >= 0 ? "text-danger" : "text-success"}>{data.institution.net_amt} 万</span>
    </p>}
  </div>;
}
