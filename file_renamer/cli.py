import os
import sys
import argparse
import logging
import fnmatch

from .config import AppConfig
from .engine import process_file, preview_file
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
    logger.info(f"监控目录: {config.watch_dir}")
    logger.info(f"加载规则数: {len(config.get_enabled_rules())}")
    for rule in config.get_enabled_rules():
        logger.info(f"  规则 [{rule.priority}]: {rule.name}")

    def handle_new_file(filepath: str):
        rules = config.get_enabled_rules()
        result = process_file(filepath, rules)
        if result:
            original_path, new_path, rule = result
            op_logger.record_rename(original_path, new_path, rule.name, True)
            return new_path
        else:
            logger.debug(f"跳过文件 (无匹配规则): {os.path.basename(filepath)}")
            return None

    watcher = FolderWatcher(
        watch_dir=config.watch_dir,
        callback=handle_new_file,
        poll_interval=config.poll_interval,
        debounce_seconds=config.debounce_seconds,
        stability_checks=config.stability_checks,
        ignored_patterns=config.ignored_patterns,
    )

    try:
        watcher.start()
    except KeyboardInterrupt:
        watcher.stop()
        logger.info("已退出守护模式")


def cmd_scan(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO, config.log_daily_rotate)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    logger.info("文件更名器 v1.0.0 - 单次扫描模式")
    logger.info(f"扫描目录: {config.watch_dir}")

    rules = config.get_enabled_rules()
    logger.info(f"加载规则数: {len(rules)}")
    for rule in rules:
        logger.info(f"  规则 [{rule.priority}]: {rule.name}")

    if not os.path.isdir(config.watch_dir):
        logger.error(f"监控目录不存在: {config.watch_dir}")
        sys.exit(1)

    processed_count = 0
    ignored = set()
    for pattern in config.ignored_patterns:
        for f in os.listdir(config.watch_dir):
            if fnmatch.fnmatch(f, pattern):
                ignored.add(f)

    for entry in os.scandir(config.watch_dir):
        if not entry.is_file():
            continue
        if entry.name in ignored:
            continue

        filepath = entry.path
        result = process_file(filepath, rules)
        if result:
            original_path, new_path, rule = result
            op_logger.record_rename(original_path, new_path, rule.name, True)
            processed_count += 1

    logger.info(f"扫描完成，共处理 {processed_count} 个文件")


def cmd_preview(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO, config.log_daily_rotate)

    rules = config.get_enabled_rules()
    if not os.path.isdir(config.watch_dir):
        logger.error(f"监控目录不存在: {config.watch_dir}")
        sys.exit(1)

    ignored = set()
    for pattern in config.ignored_patterns:
        for f in os.listdir(config.watch_dir):
            if fnmatch.fnmatch(f, pattern):
                ignored.add(f)

    entries = sorted(
        [e for e in os.scandir(config.watch_dir) if e.is_file() and e.name not in ignored],
        key=lambda e: e.name,
    )

    match_results = []
    unmatched_results = []

    for entry in entries:
        preview = preview_file(entry.path, rules)
        if preview is None:
            continue
        if preview["matched"]:
            match_results.append(preview)
        else:
            unmatched_results.append(preview)

    def _print_preview_item(p):
        size_str = _format_size(p["size"])
        if p["matched"]:
            if p["will_change"]:
                print(f"  📄 {p['filename']}  ({size_str}, {p['created']})")
                print(f"     → 规则 [{p['rule_priority']}] {p['rule_name']}")
                print(f"     → 新名: {p['new_name']}")
            else:
                print(f"  📄 {p['filename']}  ({size_str}, {p['created']})")
                print(f"     → 规则 [{p['rule_priority']}] {p['rule_name']}")
                print(f"     → 名称不变")
            if p.get("captures"):
                caps = {k: v for k, v in p["captures"].items() if v}
                if caps:
                    print(f"     → 捕获变量: {caps}")
        else:
            if not args.changed_only:
                print(f"  ⬜ {p['filename']}  ({size_str}, {p['created']})")
                print(f"     → 无匹配规则，不处理")

    print(f"\n{'=' * 90}")
    print(f"  预览模式 - 目录: {config.watch_dir}")
    print(f"  规则: {len(rules)} 条 | 文件: {len(entries)} 个")
    if args.changed_only:
        print(f"  模式: 仅显示将改名的文件")
    print(f"{'=' * 90}")

    if match_results:
        print(f"\n  --- 命中规则 ({len(match_results)} 个) ---")
        for p in match_results:
            _print_preview_item(p)
            print()

    if unmatched_results and not args.changed_only:
        print(f"  --- 无匹配 ({len(unmatched_results)} 个) ---")
        for p in unmatched_results:
            _print_preview_item(p)
            print()

    will_change = sum(1 for p in match_results if p["will_change"])
    print(f"{'=' * 90}")
    print(f"  总计: {len(entries)} 个文件 | 命中: {len(match_results)} | 将改名: {will_change} | 无匹配: {len(unmatched_results)}")
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
        logger.info(f"回滚最近 {count} 次操作")
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
            status = "✗"

        print(f"  [{r['timestamp']}] [{status}] {r['operation_id']}")
        print(f"    原始: {os.path.basename(r['original_path'])}")
        print(f"    重命名: {os.path.basename(r['new_path'])}")
        print(f"    规则: {r.get('rule', 'N/A')}")
        if r.get("rolled_back_at"):
            print(f"    回滚时间: {r['rolled_back_at']}")
        if r.get("error"):
            print(f"    错误: {r['error']}")
        print()

    if config.log_daily_rotate:
        dates = op_logger.get_available_dates()
        if dates:
            print(f"可查询日期范围: {dates[0]} ~ {dates[-1]}")


def cmd_status(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, config.log_daily_rotate)
    op_logger = OperationLogger(config.history_file, config.log_daily_rotate)

    records = op_logger.get_records()
    successful = [r for r in records if r.get("success")]
    failed = [r for r in records if not r.get("success")]
    rolled_back = [r for r in records if r.get("rolled_back")]

    print(f"\n=== 文件更名器状态 ===")
    print(f"监控目录: {config.watch_dir}")
    print(f"配置: 日志轮转={'是' if config.log_daily_rotate else '否'}, 稳定性检查={config.stability_checks}次")
    print(f"规则数量: {len(config.get_enabled_rules())}")
    print(f"总操作数: {len(records)}")
    print(f"成功: {len(successful)}, 失败: {len(failed)}, 已回滚: {len(rolled_back)}")

    active = [r for r in successful if not r.get("rolled_back")]
    if active:
        print(f"\n最近 5 条活跃操作 (未回滚):")
        for r in active[-5:]:
            print(f"  [{r['timestamp']}] {os.path.basename(r['new_path'])} (规则: {r['rule']})")

    if config.log_daily_rotate:
        dates = op_logger.get_available_dates()
        if dates:
            print(f"\n历史记录日期: {dates[0]} ~ {dates[-1]}")


def main():
    parser = argparse.ArgumentParser(
        description="文件更名器 - 监控文件夹并自动按规则重命名文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python run.py daemon -c config.yaml              # 启动守护进程
  python run.py scan -c config.yaml                 # 单次扫描目录
  python run.py preview -c config.yaml              # 预览全部文件
  python run.py preview -c config.yaml --changed    # 只看将改名的文件
  python run.py rollback -c config.yaml -n 5        # 回滚最近5次操作
  python run.py rollback -c config.yaml --dry-run -n 3  # 预览回滚
  python run.py query -c config.yaml -k 截图         # 按文件名关键字查询
  python run.py query -c config.yaml -d 20260601 -D 20260612 -r 截图  # 日期范围+规则
  python run.py query -c config.yaml --no-rolled-back  # 排除已回滚记录
  python run.py status -c config.yaml               # 查看状态
        """,
    )

    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    daemon_parser = subparsers.add_parser("daemon", help="启动守护进程，持续监控文件夹")

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
    query_parser.add_argument("--include-failed", action="store_true", help="包含失败记录")
    query_parser.add_argument("--exclude-rolled-back", "--no-rolled-back", action="store_true", help="排除已回滚记录")
    query_parser.add_argument("--limit", "-n", type=int, default=50, help="最多返回条数 (默认: 50)")

    status_parser = subparsers.add_parser("status", help="查看当前状态和操作记录")

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
        "status": cmd_status,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()