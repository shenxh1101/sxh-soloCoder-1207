import logging
import logging.handlers
import json
import os
import time
import fnmatch
import glob
from typing import Optional, List, Dict, Any

from collections import defaultdict


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


def _get_all_daily_files(base_path: str) -> List[str]:
    dirname = os.path.dirname(base_path)
    basename = os.path.basename(base_path)
    name, ext = os.path.splitext(basename)
    pattern = os.path.join(dirname, f"{name}_????????{ext}")
    return sorted(glob.glob(pattern))


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

    def _find_record_file(self, operation_id: str) -> Optional[str]:
        if self._daily_rotate:
            for filepath in _get_all_daily_files(self._history_file):
                records = self._read_records_file(filepath)
                for r in records:
                    if r.get("operation_id") == operation_id:
                        return filepath
        else:
            records = self._read_records()
            for r in records:
                if r.get("operation_id") == operation_id:
                    return self._active_file()
        return None

    def _update_record_in_file(self, filepath: str, operation_id: str, updates: dict):
        records = self._read_records_file(filepath)
        for r in records:
            if r.get("operation_id") == operation_id:
                r.update(updates)
                break
        self._write_records(records, filepath)

    def record_rename(
        self,
        original_path: str,
        new_path: str,
        rule_name: str,
        success: bool,
        error: Optional[str] = None,
        watch_label: str = "",
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
            "rolled_back": False,
            "rolled_back_at": None,
            "watch_label": watch_label,
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
        records = self._collect_all_records(None, None)
        successful = [r for r in records if r.get("success") and not r.get("rolled_back")]
        successful.sort(key=lambda r: r.get("timestamp", ""))
        if count is not None:
            return successful[-count:]
        return successful

    def get_successful_renames_today(self, count: Optional[int] = None) -> list:
        records = self._read_records()
        successful = [r for r in records if r.get("success") and not r.get("rolled_back")]
        if count is not None:
            return successful[-count:]
        return successful

    def mark_rolled_back(self, operation_ids: list):
        for op_id in operation_ids:
            filepath = self._find_record_file(op_id)
            if filepath:
                self._update_record_in_file(filepath, op_id, {
                    "rolled_back": True,
                    "rolled_back_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

    def remove_records(self, operation_ids: list):
        records = self._read_records()
        records = [r for r in records if r["operation_id"] not in operation_ids]
        self._write_records(records)

    def clear(self):
        self._write_records([])

    def query_records(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        rule: Optional[str] = None,
        keyword: Optional[str] = None,
        success_only: bool = True,
        include_rolled_back: bool = True,
        watch_label: Optional[str] = None,
        only_failed: bool = False,
        only_rolled_back: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        records = self._collect_all_records(date_from, date_to)

        if only_failed:
            records = [r for r in records if not r.get("success")]
        elif success_only:
            records = [r for r in records if r.get("success")]

        if only_rolled_back:
            records = [r for r in records if r.get("rolled_back")]
        elif not include_rolled_back:
            records = [r for r in records if not r.get("rolled_back")]

        if rule:
            records = [r for r in records if r.get("rule", "").find(rule) >= 0]

        if watch_label:
            records = [r for r in records if r.get("watch_label", "").find(watch_label) >= 0]

        if keyword:
            keyword_lower = keyword.lower()
            records = [
                r for r in records
                if keyword_lower in os.path.basename(r.get("original_path", "")).lower()
                or keyword_lower in os.path.basename(r.get("new_path", "")).lower()
                or fnmatch.fnmatch(os.path.basename(r.get("original_path", "")).lower(), f"*{keyword_lower}*")
            ]

        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

        if limit and len(records) > limit:
            records = records[:limit]

        return records

    def get_source_summary(self) -> List[Dict[str, Any]]:
        records = self._collect_all_records(None, None)

        sources: Dict[str, dict] = {}
        for r in records:
            src = r.get("watch_label", "unknown")
            if src not in sources:
                sources[src] = {
                    "label": src,
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "rolled_back": 0,
                    "last_time": "",
                }
            sources[src]["total"] += 1
            ts = r.get("timestamp", "")
            if ts > sources[src]["last_time"]:
                sources[src]["last_time"] = ts
            if r.get("success") and not r.get("rolled_back"):
                sources[src]["success"] += 1
            elif r.get("rolled_back"):
                sources[src]["rolled_back"] += 1
            else:
                sources[src]["failed"] += 1

        return sorted(sources.values(), key=lambda s: s["label"])

    def get_statistics(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        group_by: str = "rule",
    ) -> List[Dict[str, Any]]:
        records = self._collect_all_records(date_from, date_to)

        if group_by == "date":
            buckets = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "rolled_back": 0})
            for r in records:
                date_key = r.get("date", "unknown")
                buckets[date_key]["total"] += 1
                if r.get("success") and not r.get("rolled_back"):
                    buckets[date_key]["success"] += 1
                elif r.get("rolled_back"):
                    buckets[date_key]["rolled_back"] += 1
                else:
                    buckets[date_key]["failed"] += 1

            result = []
            for d in sorted(buckets.keys()):
                b = buckets[d]
                result.append({"group": d, **b})
            return result

        elif group_by == "rule":
            buckets = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "rolled_back": 0})
            for r in records:
                rule_key = r.get("rule", "unknown")
                buckets[rule_key]["total"] += 1
                if r.get("success") and not r.get("rolled_back"):
                    buckets[rule_key]["success"] += 1
                elif r.get("rolled_back"):
                    buckets[rule_key]["rolled_back"] += 1
                else:
                    buckets[rule_key]["failed"] += 1

            result = []
            for rule_name in sorted(buckets.keys(), key=lambda k: -buckets[k]["total"]):
                b = buckets[rule_name]
                result.append({"group": rule_name, **b})
            return result

        elif group_by == "source":
            buckets = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "rolled_back": 0})
            for r in records:
                src_key = r.get("watch_label", "unknown")
                buckets[src_key]["total"] += 1
                if r.get("success") and not r.get("rolled_back"):
                    buckets[src_key]["success"] += 1
                elif r.get("rolled_back"):
                    buckets[src_key]["rolled_back"] += 1
                else:
                    buckets[src_key]["failed"] += 1

            result = []
            for src in sorted(buckets.keys()):
                b = buckets[src]
                result.append({"group": src, **b})
            return result

        return []

    def _collect_all_records(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> list:
        if self._daily_rotate:
            all_files = _get_all_daily_files(self._history_file)

            if date_from or date_to:
                filtered = []
                for fp in all_files:
                    fname = os.path.basename(fp)
                    name, ext = os.path.splitext(fname)
                    date_part = name.split("_")[-1]
                    if len(date_part) == 8 and date_part.isdigit():
                        if date_from and date_part < date_from:
                            continue
                        if date_to and date_part > date_to:
                            continue
                        filtered.append(fp)
                all_files = filtered

            records = []
            for fp in all_files:
                records.extend(self._read_records_file(fp))
            return records
        else:
            records = self._read_records()
            if date_from or date_to:
                records = [
                    r for r in records
                    if (not date_from or r.get("date", "") >= date_from)
                    and (not date_to or r.get("date", "") <= date_to)
                ]
            return records

    def get_available_dates(self) -> List[str]:
        if self._daily_rotate:
            dates = []
            for fp in _get_all_daily_files(self._history_file):
                fname = os.path.basename(fp)
                name, ext = os.path.splitext(fname)
                date_part = name.split("_")[-1]
                if len(date_part) == 8 and date_part.isdigit():
                    dates.append(date_part)
            return sorted(dates)

        records = self._read_records()
        dates = list(set(r.get("date", "") for r in records if r.get("date")))
        return sorted(dates)