import os
import sys
import argparse
import logging
import fnmatch
import csv
import json
import io

from .config import AppConfig, WatchDir
from .engine import process_file, preview_file, scan_files_recursive
from .watcher import FolderWatcher
from .rollback import RollbackManager
from .logger import setup_logger, OperationLogger


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f}MB"
    else:
        return f"{size_bytes / (1024 ** 3):.1f}GB"


def cmd_daemon(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO, config.log_daily_rotate)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    logger.info("文件更名器 v1.0.0 - 守护模式")
    logger.info(f"监控目录数: {len(config.watch_dirs)}")
    for wd in config.watch_dirs:
        rules = wd.get_rules(config.global_rules)
        logger.info(f"  [{wd.label}] {wd.path} ({len(rules)} 条规则, 含子目录)")

    watchers = []
    for wd in config.watch_dirs:
        label = wd.label
        rules = wd.get_rules(config.global_rules)
        ignored = wd.get_ignored_patterns(config.ignored_patterns)

        def make_handler(wd_label, wd_rules):
            def handle_new_file(filepath: str):
                result = process_file(filepath, wd_rules)
                if result is None:
                    logger.debug(f"[{wd_label}] 跳过文件 (无匹配规则): {os.path.basename(filepath)}")
                    return None
                if result["success"]:
                    op_logger.record_rename(
                        result["original_path"], result["new_path"],
                        result["rule"].name, True, watch_label=wd_label,
                    )
                    return result["new_path"]
                else:
                    op_logger.record_rename(
                        result["original_path"], result["new_path"],
                        result["rule"].name, False, error=result["error"],
                        watch_label=wd_label,
                    )
                    return None
            return handle_new_file

        watcher = FolderWatcher(
            watch_dir=wd.path,
            callback=make_handler(label, rules),
            poll_interval=config.poll_interval,
            debounce_seconds=config.debounce_seconds,
            stability_checks=config.stability_checks,
            ignored_patterns=ignored,
        )
        watchers.append(watcher)

    import threading
    threads = []
    for w in watchers:
        t = threading.Thread(target=w.start, daemon=True)
        t.start()
        threads.append(t)

    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        for w in watchers:
            w.stop()
        logger.info("已退出守护模式")


def cmd_scan(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO, config.log_daily_rotate)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    logger.info("文件更名器 v1.0.0 - 单次扫描模式")
    logger.info(f"扫描目录数: {len(config.watch_dirs)}")

    total_processed = 0
    total_failed = 0
    for wd in config.watch_dirs:
        rules = wd.get_rules(config.global_rules)
        ignored = wd.get_ignored_patterns(config.ignored_patterns)
        logger.info(f"  [{wd.label}] {wd.path} ({len(rules)} 条规则)")

        if not os.path.isdir(wd.path):
            logger.error(f"目录不存在: {wd.path}")
            continue

        files = scan_files_recursive(wd.path, ignored)
        for filepath in files:
            result = process_file(filepath, rules)
            if result is None:
                continue
            if result["success"]:
                op_logger.record_rename(
                    result["original_path"], result["new_path"],
                    result["rule"].name, True, watch_label=wd.label,
                )
                total_processed += 1
            else:
                op_logger.record_rename(
                    result["original_path"], result["new_path"],
                    result["rule"].name, False, error=result["error"],
                    watch_label=wd.label,
                )
                total_failed += 1

    logger.info(f"扫描完成，成功: {total_processed}, 失败: {total_failed}")


def cmd_preview(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO, config.log_daily_rotate)

    all_match_results = []
    all_unmatched_results = []

    for wd in config.watch_dirs:
        rules = wd.get_rules(config.global_rules)
        ignored = wd.get_ignored_patterns(config.ignored_patterns)

        if not os.path.isdir(wd.path):
            logger.error(f"目录不存在: {wd.path}")
            continue

        files = scan_files_recursive(wd.path, ignored)
        for filepath in files:
            preview = preview_file(filepath, rules, watch_label=wd.label)
            if preview is None:
                continue
            if preview["matched"]:
                all_match_results.append(preview)
            else:
                all_unmatched_results.append(preview)

    def _print_preview_item(p):
        size_str = _format_size(p["size"])
        label = f" [{p.get('watch_label', '')}]" if p.get("watch_label") else ""
        if p["matched"]:
            if p["will_change"]:
                print(f"  📄 {p['filename']}{label}  ({size_str}, {p['created']})")
                print(f"     → 规则 [{p['rule_priority']}] {p['rule_name']}")
                print(f"     → 新名: {p['new_name']}")
            else:
                if not args.changed_only:
                    print(f"  📄 {p['filename']}{label}  ({size_str}, {p['created']})")
                    print(f"     → 规则 [{p['rule_priority']}] {p['rule_name']}")
                    print(f"     → 名称不变")
            if p.get("captures") and not args.changed_only:
                caps = {k: v for k, v in p["captures"].items() if v}
                if caps:
                    print(f"     → 捕获变量: {caps}")
        else:
            if not args.changed_only:
                print(f"  ⬜ {p['filename']}{label}  ({size_str}, {p['created']})")
                print(f"     → 无匹配规则，不处理")

    will_change_items = [p for p in all_match_results if p["will_change"]]
    name_unchanged = [p for p in all_match_results if not p["will_change"]]

    print(f"\n{'=' * 90}")
    print(f"  预览模式 - 目录: {len(config.watch_dirs)} 个")
    for wd in config.watch_dirs:
        print(f"    [{wd.label}] {wd.path}")
    print(f"  文件: {len(all_match_results) + len(all_unmatched_results)} 个")
    if args.changed_only:
        print(f"  模式: 仅显示将改名的文件")
    print(f"{'=' * 90}")

    if args.changed_only:
        if will_change_items:
            print(f"\n  --- 将改名 ({len(will_change_items)} 个) ---")
            for p in will_change_items:
                _print_preview_item(p)
                print()
        else:
            print(f"\n  (没有文件将被改名)")
    else:
        if will_change_items:
            print(f"\n  --- 将改名 ({len(will_change_items)} 个) ---")
            for p in will_change_items:
                _print_preview_item(p)
                print()
        if name_unchanged:
            print(f"  --- 命中规则但名称不变 ({len(name_unchanged)} 个) ---")
            for p in name_unchanged:
                _print_preview_item(p)
                print()
        if all_unmatched_results:
            print(f"  --- 无匹配 ({len(all_unmatched_results)} 个) ---")
            for p in all_unmatched_results:
                _print_preview_item(p)
                print()

    print(f"{'=' * 90}")
    print(f"  总计: {len(all_match_results) + len(all_unmatched_results)} 个文件 | 命中: {len(all_match_results)} | 将改名: {len(will_change_items)} | 无匹配: {len(all_unmatched_results)}")
    print(f"{'=' * 90}\n")


def cmd_rollback(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO, config.log_daily_rotate)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    rollback_mgr = RollbackManager(op_logger)

    if args.show_history:
        rollback_mgr.show_history(args.count)
        return

    if args.operation_ids:
        logger.info(f"按ID回滚: {args.operation_ids}")
        results = rollback_mgr.rollback_by_ids(args.operation_ids, dry_run=args.dry_run)
    else:
        count = args.count if args.count else 1
        logger.info(f"回滚最近 {count} 次操作 (跨天搜索)")
        results = rollback_mgr.rollback_recent(count, dry_run=args.dry_run)

    success_count = sum(1 for r in results if r.get("success"))
    fail_count = sum(1 for r in results if not r.get("success"))

    if args.dry_run:
        logger.info(f"预览完成: {success_count} 个可回滚")
    else:
        logger.info(f"回滚完成: {success_count} 成功, {fail_count} 失败")


def cmd_query(args):
    config = AppConfig.from_file(args.config)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    records = op_logger.query_records(
        date_from=args.date_from,
        date_to=args.date_to,
        rule=args.rule,
        keyword=args.keyword,
        success_only=not args.include_failed,
        include_rolled_back=not args.exclude_rolled_back,
        watch_label=args.source,
        only_failed=args.only_failed,
        only_rolled_back=args.only_rolled_back,
        limit=args.limit,
    )

    if not records:
        print("没有找到匹配的记录")
        return

    print(f"\n找到 {len(records)} 条记录 (按时间倒序):")
    print("-" * 100)
    for r in records:
        if r.get("rolled_back"):
            status = "↩ 已回滚"
        elif r.get("success"):
            status = "✓"
        else:
            status = "✗ 失败"

        print(f"  [{r['timestamp']}] [{status}] {r['operation_id']}")
        print(f"    原始: {os.path.basename(r['original_path'])}")
        print(f"    重命名: {os.path.basename(r['new_path'])}")
        print(f"    规则: {r.get('rule', 'N/A')}")
        if r.get("watch_label"):
            print(f"    来源: {r['watch_label']}")
        if r.get("rolled_back_at"):
            print(f"    回滚时间: {r['rolled_back_at']}")
        if r.get("error"):
            print(f"    错误: {r['error']}")
        print()

    if config.log_daily_rotate:
        dates = op_logger.get_available_dates()
        if dates:
            print(f"可查询日期范围: {dates[0]} ~ {dates[-1]}")


def cmd_stats(args):
    config = AppConfig.from_file(args.config)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    stats = op_logger.get_statistics(
        date_from=args.date_from,
        date_to=args.date_to,
        group_by=args.group_by,
    )

    if not stats:
        print("没有统计数据")
        return

    group_name = {"date": "日期", "rule": "规则", "source": "来源目录"}.get(args.group_by, args.group_by)

    print(f"\n{'=' * 80}")
    print(f"  统计 (按{group_name}分组)")
    if args.date_from or args.date_to:
        print(f"  日期范围: {args.date_from or '最早'} ~ {args.date_to or '最晚'}")
    print(f"{'=' * 80}")
    print(f"  {'分组':<30} {'总计':>6} {'成功':>6} {'失败':>6} {'回滚':>6}")
    print(f"  {'-' * 30} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6}")

    total_all = 0
    success_all = 0
    failed_all = 0
    rolled_all = 0
    for s in stats:
        group = s["group"]
        if len(group) > 28:
            group = group[:27] + "..."
        print(f"  {group:<30} {s['total']:>6} {s['success']:>6} {s['failed']:>6} {s['rolled_back']:>6}")
        total_all += s["total"]
        success_all += s["success"]
        failed_all += s["failed"]
        rolled_all += s["rolled_back"]

    print(f"  {'─' * 30} {'─' * 6} {'─' * 6} {'─' * 6} {'─' * 6}")
    print(f"  {'合计':<30} {total_all:>6} {success_all:>6} {failed_all:>6} {rolled_all:>6}")
    print(f"{'=' * 80}\n")


def cmd_export(args):
    config = AppConfig.from_file(args.config)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    records = op_logger.query_records(
        date_from=args.date_from,
        date_to=args.date_to,
        rule=args.rule,
        success_only=not args.include_failed,
        include_rolled_back=True,
        watch_label=args.source,
        only_failed=args.only_failed,
        only_rolled_back=args.only_rolled_back,
        limit=0,
    )

    if not records:
        print("没有找到匹配的记录")
        return

    records.sort(key=lambda r: r.get("timestamp", ""))

    output_path = args.output
    if not output_path:
        if args.format == "csv":
            output_path = "rename_export.csv"
        else:
            output_path = "rename_export.json"

    if args.format == "csv":
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["时间", "操作ID", "文件名(原)", "文件名(新)", "规则", "来源目录", "是否回滚", "回滚时间", "成功", "错误"])
            for r in records:
                writer.writerow([
                    r.get("timestamp", ""),
                    r.get("operation_id", ""),
                    os.path.basename(r.get("original_path", "")),
                    os.path.basename(r.get("new_path", "")),
                    r.get("rule", ""),
                    r.get("watch_label", ""),
                    "是" if r.get("rolled_back") else "否",
                    r.get("rolled_back_at", ""),
                    "是" if r.get("success") else "否",
                    r.get("error", ""),
                ])
    else:
        export_data = []
        for r in records:
            export_data.append({
                "timestamp": r.get("timestamp", ""),
                "operation_id": r.get("operation_id", ""),
                "original_name": os.path.basename(r.get("original_path", "")),
                "new_name": os.path.basename(r.get("new_path", "")),
                "original_path": r.get("original_path", ""),
                "new_path": r.get("new_path", ""),
                "rule": r.get("rule", ""),
                "watch_label": r.get("watch_label", ""),
                "rolled_back": r.get("rolled_back", False),
                "rolled_back_at": r.get("rolled_back_at"),
                "success": r.get("success", False),
                "error": r.get("error"),
            })
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

    date_info = ""
    if args.date_from or args.date_to:
        date_info = f" ({args.date_from or '最早'} ~ {args.date_to or '最晚'})"
    filter_info = ""
    filters = []
    if args.source: filters.append(f"来源={args.source}")
    if args.rule: filters.append(f"规则={args.rule}")
    if args.only_failed: filters.append("仅失败")
    if args.only_rolled_back: filters.append("仅回滚")
    if filters: filter_info = f" [筛选: {', '.join(filters)}]"
    print(f"导出完成: {len(records)} 条记录{date_info}{filter_info} -> {output_path}")


def cmd_status(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, config.log_daily_rotate)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    records = op_logger.get_records()
    successful = [r for r in records if r.get("success")]
    failed = [r for r in records if not r.get("success")]
    rolled_back = [r for r in records if r.get("rolled_back")]
    active = [r for r in successful if not r.get("rolled_back")]

    print(f"\n{'=' * 70}")
    print(f"  文件更名器 - 服务状态")
    print(f"{'=' * 70}")
    print(f"  配置: 日志轮转={'是' if config.log_daily_rotate else '否'}, 稳定性检查={config.stability_checks}次")
    print(f"  全局: 总计 {len(records)} | 成功 {len(successful)} | 失败 {len(failed)} | 已回滚 {len(rolled_back)} | 活跃 {len(active)}")

    summary = op_logger.get_source_summary()
    if summary:
        print(f"\n  {'─' * 70}")
        print(f"  {'来源目录':<18} {'总计':>6} {'成功':>6} {'失败':>6} {'回滚':>6}  {'最近处理时间':<20}")
        print(f"  {'─' * 70}")
        for s in summary:
            label = s["label"] if len(s["label"]) <= 16 else s["label"][:15] + "…"
            last = s["last_time"] if s["last_time"] else "从未"
            print(f"  {label:<18} {s['total']:>6} {s['success']:>6} {s['failed']:>6} {s['rolled_back']:>6}  {last:<20}")

        for wd in config.watch_dirs:
            has_records = any(s["label"] == wd.label for s in summary)
            if not has_records:
                print(f"  {wd.label:<18} {'0':>6} {'0':>6} {'0':>6} {'0':>6}  {'无记录':<20}")

    if active:
        print(f"\n  最近 5 条活跃操作:")
        for r in active[-5:]:
            label = f" [{r.get('watch_label', '')}]" if r.get("watch_label") else ""
            print(f"    [{r['timestamp']}]{label} {os.path.basename(r['new_path'])} (规则: {r['rule']})")

    if config.log_daily_rotate:
        dates = op_logger.get_available_dates()
        if dates:
            print(f"\n  历史日期: {dates[0]} ~ {dates[-1]}")
    print(f"{'=' * 70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="文件更名器 - 监控文件夹并自动按规则重命名文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python run.py daemon -c config.yaml                # 启动守护进程 (含子目录)
  python run.py scan -c config.yaml                   # 单次扫描全部目录
  python run.py preview -c config.yaml                # 预览全部目录
  python run.py preview -c config.yaml --changed      # 只看将改名的文件
  python run.py rollback -c config.yaml -n 5          # 回滚最近5次操作
  python run.py query -c config.yaml -k 截图           # 按文件名关键字查询
  python run.py query -c config.yaml --only-failed    # 只看失败记录
  python run.py stats -c config.yaml -g source        # 按来源统计
  python run.py export -c config.yaml -s 截图目录 --only-failed  # 导出失败记录
  python run.py status -c config.yaml                 # 查看服务状态
        """,
    )

    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    daemon_parser = subparsers.add_parser("daemon", help="启动守护进程，持续监控文件夹(含子目录)")

    scan_parser = subparsers.add_parser("scan", help="单次扫描目录并处理已有文件")

    preview_parser = subparsers.add_parser("preview", help="预览规则匹配结果，不实际重命名")
    preview_parser.add_argument("--changed-only", "--changed", action="store_true", help="仅显示将改名的文件")

    rollback_parser = subparsers.add_parser("rollback", help="回滚重命名操作")
    rollback_parser.add_argument("--count", "-n", type=int, default=1, help="回滚最近 N 次操作 (默认: 1)")
    rollback_parser.add_argument("--id", dest="operation_ids", action="append", help="按操作ID回滚 (可多次使用)")
    rollback_parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行回滚")
    rollback_parser.add_argument("--history", dest="show_history", action="store_true", help="显示最近操作记录")

    query_parser = subparsers.add_parser("query", help="查询历史操作记录")
    query_parser.add_argument("--date-from", "-d", help="起始日期 (YYYYMMDD)")
    query_parser.add_argument("--date-to", "-D", help="结束日期 (YYYYMMDD)")
    query_parser.add_argument("--rule", "-r", help="按规则名筛选")
    query_parser.add_argument("--keyword", "-k", help="文件名关键字搜索")
    query_parser.add_argument("--source", "-s", help="按来源目录筛选")
    query_parser.add_argument("--include-failed", action="store_true", help="包含失败记录")
    query_parser.add_argument("--only-failed", action="store_true", help="仅显示失败记录")
    query_parser.add_argument("--only-rolled-back", action="store_true", help="仅显示已回滚记录")
    query_parser.add_argument("--exclude-rolled-back", "--no-rolled-back", action="store_true", help="排除已回滚记录")
    query_parser.add_argument("--limit", "-n", type=int, default=50, help="最多返回条数 (默认: 50)")

    stats_parser = subparsers.add_parser("stats", help="查看操作统计")
    stats_parser.add_argument("--date-from", "-d", help="起始日期 (YYYYMMDD)")
    stats_parser.add_argument("--date-to", "-D", help="结束日期 (YYYYMMDD)")
    stats_parser.add_argument("--group-by", "-g", choices=["date", "rule", "source"], default="rule", help="分组方式 (默认: rule)")

    export_parser = subparsers.add_parser("export", help="导出历史记录")
    export_parser.add_argument("--date-from", "-d", help="起始日期 (YYYYMMDD)")
    export_parser.add_argument("--date-to", "-D", help="结束日期 (YYYYMMDD)")
    export_parser.add_argument("--format", "-f", choices=["csv", "json"], default="csv", help="导出格式 (默认: csv)")
    export_parser.add_argument("--output", "-o", help="输出文件路径")
    export_parser.add_argument("--source", "-s", help="按来源目录筛选")
    export_parser.add_argument("--rule", "-r", help="按规则名筛选")
    export_parser.add_argument("--include-failed", action="store_true", help="包含失败记录")
    export_parser.add_argument("--only-failed", action="store_true", help="仅导出失败记录")
    export_parser.add_argument("--only-rolled-back", action="store_true", help="仅导出已回滚记录")

    status_parser = subparsers.add_parser("status", help="查看服务状态 (含每目录统计)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "daemon": cmd_daemon,
        "scan": cmd_scan,
        "preview": cmd_preview,
        "rollback": cmd_rollback,
        "query": cmd_query,
        "stats": cmd_stats,
        "export": cmd_export,
        "status": cmd_status,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()