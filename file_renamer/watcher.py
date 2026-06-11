import os
import time
import logging
import fnmatch
from typing import Callable, Optional

logger = logging.getLogger("file_renamer")


class FolderWatcher:
    def __init__(
        self,
        watch_dir: str,
        callback: Callable[[str], None],
        poll_interval: float = 1.0,
        debounce_seconds: float = 2.0,
        ignored_patterns: Optional[list] = None,
    ):
        if not os.path.isdir(watch_dir):
            raise ValueError(f"监控目录不存在: {watch_dir}")

        self._watch_dir = os.path.abspath(watch_dir)
        self._callback = callback
        self._poll_interval = poll_interval
        self._debounce_seconds = debounce_seconds
        self._ignored_patterns = ignored_patterns or []
        self._known_files: dict = {}
        self._pending_files: dict = {}
        self._running = False

    def _is_ignored(self, filename: str) -> bool:
        for pattern in self._ignored_patterns:
            if fnmatch.fnmatch(filename, pattern):
                return True
        return False

    def _scan_directory(self) -> set:
        current_files = set()
        for entry in os.scandir(self._watch_dir):
            if entry.is_file() and not self._is_ignored(entry.name):
                current_files.add(entry.path)
        return current_files

    def start(self):
        logger.info(f"开始监控目录: {self._watch_dir}")
        logger.info(f"轮询间隔: {self._poll_interval}s, 防抖: {self._debounce_seconds}s")

        self._known_files = {}
        existing = self._scan_directory()
        for f in existing:
            self._known_files[f] = os.path.getmtime(f)
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
        try:
            current_files = self._scan_directory()
        except FileNotFoundError:
            logger.error(f"监控目录不可访问: {self._watch_dir}")
            return
        except PermissionError:
            logger.error(f"监控目录无权限: {self._watch_dir}")
            return

        current_time = time.time()

        new_files = current_files - set(self._known_files.keys())
        for filepath in new_files:
            self._handle_new_file(filepath, current_time)

        missing_files = set(self._known_files.keys()) - current_files
        for filepath in missing_files:
            del self._known_files[filepath]
            self._pending_files.pop(filepath, None)

        for filepath in current_files:
            current_mtime = os.path.getmtime(filepath)
            known_mtime = self._known_files.get(filepath)
            if known_mtime is not None and current_mtime != known_mtime:
                self._known_files[filepath] = current_mtime

        self._process_pending(current_time)

    def _handle_new_file(self, filepath: str, current_time: float):
        self._pending_files[filepath] = current_time
        logger.debug(f"检测到新文件(等待稳定): {os.path.basename(filepath)}")

    def _process_pending(self, current_time: float):
        ready = []
        for filepath, detected_time in list(self._pending_files.items()):
            elapsed = current_time - detected_time
            if elapsed >= self._debounce_seconds:
                if os.path.exists(filepath):
                    ready.append(filepath)
                del self._pending_files[filepath]

        for filepath in ready:
            self._known_files[filepath] = os.path.getmtime(filepath)
            logger.info(f"新文件已稳定: {os.path.basename(filepath)}")
            try:
                self._callback(filepath)
            except Exception as e:
                logger.error(f"处理文件异常 '{filepath}': {e}")

    def stop(self):
        self._running = False
        logger.info("监控器正在停止...")