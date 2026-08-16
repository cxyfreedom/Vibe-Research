# PostgreSQL 运维

数据库物理目录默认映射到项目 `data/postgres/`。备份和恢复前先确认
`VR_DATABASE_URL` 指向当前 Vibe-Research 数据库。

## 备份

推荐每天在全量个股任务结束后执行自定义格式备份。项目默认通过 Docker
运行 PostgreSQL，因此无需在宿主机额外安装客户端：

```bash
mkdir -p data/backups
docker compose exec -T postgres pg_dump -U vibe -d vibe_research --format=custom \
  --file /tmp/vibe-research.dump
docker cp vibe-research-postgres:/tmp/vibe-research.dump "data/backups/vibe-$(date +%F).dump"
docker compose exec -T postgres rm -f /tmp/vibe-research.dump
```

备份文件包含 schema migration、市场历史、个股日快照和采集状态。`data/` 已被 Git 忽略。
若宿主机已安装 PostgreSQL 客户端，也可以直接执行
`pg_dump "$VR_DATABASE_URL" --format=custom --file <目标文件>`。

## 恢复

恢复会覆盖同名对象，操作前应先停止后端采集进程并额外保留一份当前库备份：

```bash
docker cp data/backups/vibe-YYYY-MM-DD.dump vibe-research-postgres:/tmp/vibe-restore.dump
docker compose exec -T postgres pg_restore --clean --if-exists --no-owner \
  -U vibe -d vibe_research /tmp/vibe-restore.dump
docker compose exec -T postgres rm -f /tmp/vibe-restore.dump
```

恢复后启动后端，`market_store.init_db()` 会自动补齐缺少的 schema migration。

## 数据保留

- `stock_data_snapshots` 是按交易日归档的回溯数据，默认长期保留。
- `market_news` 使用内容哈希去重，默认长期保留。
- 盘中 `fund_flow` 快照默认保留 90 天，可通过 `VR_FUND_FLOW_RETENTION_DAYS` 调整。
- 删除历史前先通过“数据健康”页面确认覆盖日期并完成备份。

## 故障恢复

- 全量个股任务按 `代码 + 数据类型 + 交易日` 记录断点，重新运行会跳过成功项。
- 21:00 自动运行 `stock_archive_retry` 补采失败项，也可在“数据健康”页面手动触发。
- 超过 6 小时仍为 `running` 的旧运行记录会在服务启动时标记为失败，不会阻塞新任务。
- PostgreSQL advisory lock 保证不同后端进程不会同时执行同名采集任务。
