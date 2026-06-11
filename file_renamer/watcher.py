import os
import time
import logging
import fnmatch
from typing import Callable, Optional, Set

logger = logging.getLogger("file_renamer")


class FolderWatcher:
    def __init__(
        self,
        watch_dir: str,
        callback: Callable[[str], Optional[str]],
        poll_interval: float = 1.0,
        debounce_seconds: float = 2.0,
        stability_checks: int = 3,
        ignored_patterns: Optional[list] = None,
    ):
        if not os.path.isdir(watch_dir):
            raise ValueError(f"监控目录不存在: {watch_dir}")

        self._watch_dir = os.path.abspath(watch_dir)
        self._callback = callback
        self._poll_interval = poll_interval
        self._debounce_seconds = debounce_seconds
        self._stability_checks = stability_checks
        self._ignored_patterns = ignored_patterns or []
        self._known_files: dict[str, float] = {}
        self._pending_files: dict[str, dict] = {}
        self._processed_paths: Set[str] = set()
        self._running = False

    def _is_ignored(self, filename: str) -> bool:
        for pattern in self._ignored_patterns:
            if fnmatch.fnmatch(filename, pattern):
                return True
        return False

    def _scan_directory(self) -> set:
        current_files = set()
        try:
            for entry in os.scandir(self._watch_dir):
                if entry.is_file(follow_symlinks=False) and not self._is_ignored(entry.name):
                    current_files.add(entry.path)
        except FileNotFoundError:
            logger.error(f"监控目录不可访问: {self._watch_dir}")
        except PermissionError:
            logger.error(f"监控目录无权限: {self._watch_dir}")
        return current_files

    def mark_processed(self, filepath: str):
        abs_path = os.path.abspath(filepath)
        self._processed_paths.add(abs_path)
        self._known_files[abs_path] = os.path.getmtime(abs_path)

    def start(self):
        logger.info(f"开始监控目录: {self._watch_dir}")
        logger.info(f"轮询间隔: {self._poll_interval}s, 防抖: {self._debounce_seconds}s, 稳定性检查: {self._stability_checks}次")

        self._known_files = {}
        self._processed_paths = set()
        existing = self._scan_directory()
        for f in existing:
            self._known_files[f] = os.path.getmtime(f)
            self._processed_paths.add(f)
        logger.debug(f"初始扫描: 发现 {len(existing)} 个已有文件")

        self._running = True

        try:
            while self._running:
                self._poll()
                time.sleep(self._poll_interval)
        except KeyboardInterrupt:
            logger.info("收到中断信号，停止监控")
        finally:
            self._running = False
            logger.info("监控已停止")

    def _poll(self):
        current_files = self._scan_directory()
        current_time = time.time()

        new_files = current_files - set(self._known_files.keys())
        for filepath in new_files:
            self._handle_new_file(filepath, current_time)

        missing_files = set(self._known_files.keys()) - current_files
        for filepath in missing_files:
            self._known_files.pop(filepath, None)
            self._pending_files.pop(filepath, None)
            self._processed_paths.discard(filepath)

        self._process_pending(current_time)

    def _handle_new_file(self, filepath: str, current_time: float):
        self._pending_files[filepath] = {
            "detected_time": current_time,
            "last_size": -1,
            "stable_count": 0,
        }
        logger.debug(f"检测到新文件(等待稳定): {os.path.basename(filepath)}")

    def _process_pending(self, current_time: float):
        ready = []
        for filepath, info in list(self._pending_files.items()):
            elapsed = current_time - info["detected_time"]

            if elapsed < self._debounce_seconds:
                continue

            if not os.path.exists(filepath):
                del self._pending_files[filepath]
                continue

            try:
                current_size = os.path.getsize(filepath)
            except OSError:
                del self._pending_files[filepath]
                continue

            if current_size == info["last_size"]:
                info["stable_count"] += 1
            else:
                info["last_size"] = current_size
                info["stable_count"] = 0
                logger.debug(f"文件大小变化 {os.path.basename(filepath)}: {current_size} bytes, 重置稳定性计数")

            if info["stable_count"] >= self._stability_checks:
                ready.append(filepath)
                del self._pending_files[filepath]

        for filepath in ready:
            abs_path = os.path.abspath(filepath)
            if abs_path in self._processed_paths:
                logger.debug(f"跳过已处理的文件: {os.path.basename(filepath)}")
                self._known_files[abs_path] = os.path.getmtime(abs_path)
                continue

            self._known_files[abs_path] = os.path.getmtime(abs_path)
            logger.info(f"新文件已稳定: {os.path.basename(filepath)}")
            try:
                result = self._callback(filepath)
                if result:
                    self._processed_paths.add(abs_path)
                    new_abs = os.path.abspath(result)
                    self._processed_paths.add(new_abs)
                    if os.path.exists(new_abs):
                        self._known_files[new_abs] = os.path.getmtime(new_abs)
            except Exception as e:
                logger.error(f"处理文件异常 '{filepath}': {e}")

    def stop(self):
        self._running = False
        logger.info("监控器正在停止...")