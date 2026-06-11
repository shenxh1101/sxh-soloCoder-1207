import logging
import logging.handlers
import json
import os
import time
import fnmatch
from typing import Optional, List, Dict, Any


def setup_logger(log_file: str, level: int = logging.INFO, daily_rotate: bool = False) -> logging.Logger:
    logger = logging.getLogger("file_renamer")
    logger.setLevel(level)

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        if daily_rotate:
            file_handler = logging.handlers.TimedRotatingFileHandler(
                log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
            )
            file_handler.suffix = "%Y%m%d"
        else:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")

        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def _get_daily_history_path(base_path: str, date_str: Optional[str] = None) -> str:
    if date_str is None:
        date_str = time.strftime("%Y%m%d")
    dirname = os.path.dirname(base_path)
    basename = os.path.basename(base_path)
    name, ext = os.path.splitext(basename)
    daily_name = f"{name}_{date_str}{ext}"
    return os.path.join(dirname, daily_name)


class OperationLogger:
    def __init__(self, history_file: str, daily_rotate: bool = False):
        self._history_file = history_file
        self._daily_rotate = daily_rotate

    def _active_file(self) -> str:
        if self._daily_rotate:
            return _get_daily_history_path(self._history_file)
        return self._history_file

    def _ensure_file(self, filepath: str):
        if not os.path.exists(filepath):
            history_dir = os.path.dirname(filepath)
            if history_dir and not os.path.exists(history_dir):
                os.makedirs(history_dir, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    def _read_records_file(self, filepath: str) -> list:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _read_records(self) -> list:
        return self._read_records_file(self._active_file())

    def _write_records(self, records: list, filepath: Optional[str] = None):
        target = filepath or self._active_file()
        with open(target, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    def record_rename(
        self,
        original_path: str,
        new_path: str,
        rule_name: str,
        success: bool,
        error: Optional[str] = None,
    ) -> str:
        operation_id = f"op_{int(time.time() * 1000000)}"
        record = {
            "operation_id": operation_id,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "date": time.strftime("%Y%m%d"),
            "original_path": os.path.abspath(original_path),
            "new_path": os.path.abspath(new_path),
            "rule": rule_name,
            "success": success,
            "error": error,
        }

        records = self._read_records()
        records.append(record)
        self._write_records(records)

        return operation_id

    def get_records(self, count: Optional[int] = None) -> list:
        records = self._read_records()
        if count is not None:
            return records[-count:]
        return records

    def get_successful_renames(self, count: Optional[int] = None) -> list:
        records = self._read_records()
        successful = [r for r in records if r.get("success")]
        if count is not None:
            return successful[-count:]
        return successful

    def remove_records(self, operation_ids: list):
        records = self._read_records()
        records = [r for r in records if r["operation_id"] not in operation_ids]
        self._write_records(records)

    def clear(self):
        self._write_records([])

    def query_records(
        self,
        date_str: Optional[str] = None,
        rule: Optional[str] = None,
        keyword: Optional[str] = None,
        success_only: bool = True,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if date_str and self._daily_rotate:
            filepath = _get_daily_history_path(self._history_file, date_str)
            records = self._read_records_file(filepath)
        elif date_str and not self._daily_rotate:
            records = self._read_records()
            if date_str:
                records = [r for r in records if r.get("date", "") == date_str]
        else:
            records = self._read_records()

        if success_only:
            records = [r for r in records if r.get("success")]

        if rule:
            records = [r for r in records if r.get("rule", "").find(rule) >= 0]

        if keyword:
            records = [
                r for r in records
                if keyword.lower() in os.path.basename(r.get("original_path", "")).lower()
                or keyword.lower() in os.path.basename(r.get("new_path", "")).lower()
                or fnmatch.fnmatch(os.path.basename(r.get("original_path", "")).lower(), f"*{keyword.lower()}*")
            ]

        if limit and len(records) > limit:
            records = records[-limit:]

        return records

    def get_available_dates(self) -> List[str]:
        if not self._daily_rotate:
            return []

        dirname = os.path.dirname(self._history_file)
        basename = os.path.basename(self._history_file)
        name, ext = os.path.splitext(basename)
        prefix = f"{name}_"

        dates = []
        if not os.path.isdir(dirname):
            return []

        for f in os.listdir(dirname):
            if f.startswith(prefix) and f.endswith(ext):
                date_part = f[len(prefix):-len(ext)]
                if len(date_part) == 8 and date_part.isdigit():
                    dates.append(date_part)

        return sorted(dates)