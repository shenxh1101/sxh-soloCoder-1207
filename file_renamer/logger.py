import logging
import json
import os
import time
from typing import Optional, Dict, Any


def setup_logger(log_file: str, level: int = logging.INFO) -> logging.Logger:
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

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


class OperationLogger:
    def __init__(self, history_file: str):
        self._history_file = history_file
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self._history_file):
            history_dir = os.path.dirname(self._history_file)
            if history_dir and not os.path.exists(history_dir):
                os.makedirs(history_dir, exist_ok=True)
            self._write_records([])

    def _read_records(self) -> list:
        try:
            with open(self._history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write_records(self, records: list):
        with open(self._history_file, "w", encoding="utf-8") as f:
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