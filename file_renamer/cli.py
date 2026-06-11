import os
import sys
import argparse
import logging

from .config import AppConfig
from .engine import process_file
from .watcher import FolderWatcher
from .rollback import RollbackManager
from .logger import setup_logger, OperationLogger


def cmd_daemon(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO)
    op_logger = OperationLogger(config.history_file)

    logger.info(f"文件更名器 v1.0.0 - 守护模式")
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
        else:
            logger.debug(f"跳过文件 (无匹配规则): {os.path.basename(filepath)}")

    watcher = FolderWatcher(
        watch_dir=config.watch_dir,
        callback=handle_new_file,
        poll_interval=config.poll_interval,
        debounce_seconds=config.debounce_seconds,
        ignored_patterns=config.ignored_patterns,
    )

    try:
        watcher.start()
    except KeyboardInterrupt:
        watcher.stop()
        logger.info("已退出守护模式")


def cmd_scan(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO)
    op_logger = OperationLogger(config.history_file)

    logger.info(f"文件更名器 v1.0.0 - 单次扫描模式")
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
        import fnmatch
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


def cmd_rollback(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file, logging.DEBUG if args.verbose else logging.INFO)
    op_logger = OperationLogger(config.history_file)

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


def cmd_status(args):
    config = AppConfig.from_file(args.config)
    logger = setup_logger(config.log_file)
    op_logger = OperationLogger(config.history_file)

    records = op_logger.get_records()
    successful = [r for r in records if r.get("success")]
    failed = [r for r in records if not r.get("success")]

    print(f"\n=== 文件更名器状态 ===")
    print(f"监控目录: {config.watch_dir}")
    print(f"规则数量: {len(config.get_enabled_rules())}")
    print(f"总操作数: {len(records)}")
    print(f"成功: {len(successful)}, 失败: {len(failed)}")

    if successful:
        print(f"\n最近 5 条成功操作:")
        for r in successful[-5:]:
            print(f"  [{r['timestamp']}] {os.path.basename(r['new_path'])} (规则: {r['rule']})")


def main():
    parser = argparse.ArgumentParser(
        description="文件更名器 - 监控文件夹并自动按规则重命名文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python run.py daemon --config config.yaml          # 启动守护进程
  python run.py scan --config config.yaml             # 单次扫描目录
  python run.py rollback --config config.yaml -n 5    # 回滚最近5次操作
  python run.py rollback --config config.yaml --dry-run -n 3  # 预览回滚
  python run.py status --config config.yaml           # 查看状态
        """,
    )

    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    daemon_parser = subparsers.add_parser("daemon", help="启动守护进程，持续监控文件夹")

    scan_parser = subparsers.add_parser("scan", help="单次扫描目录并处理已有文件")

    rollback_parser = subparsers.add_parser("rollback", help="回滚重命名操作")
    rollback_parser.add_argument("--count", "-n", type=int, default=1, help="回滚最近 N 次操作 (默认: 1)")
    rollback_parser.add_argument("--id", dest="operation_ids", action="append", help="按操作ID回滚 (可多次使用)")
    rollback_parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行回滚")
    rollback_parser.add_argument("--history", dest="show_history", action="store_true", help="显示最近操作记录")

    status_parser = subparsers.add_parser("status", help="查看当前状态和操作记录")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "daemon": cmd_daemon,
        "scan": cmd_scan,
        "rollback": cmd_rollback,
        "status": cmd_status,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()