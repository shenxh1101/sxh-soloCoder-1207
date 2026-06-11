import os
import logging
from typing import List, Optional

from .logger import OperationLogger

logger = logging.getLogger("file_renamer")


class RollbackManager:
    def __init__(self, operation_logger: OperationLogger):
        self._op_logger = operation_logger

    def rollback_recent(self, count: int, dry_run: bool = False) -> List[dict]:
        records = self._op_logger.get_successful_renames(count)
        return self._do_rollback(records, dry_run)

    def rollback_by_ids(self, operation_ids: List[str], dry_run: bool = False) -> List[dict]:
        all_records = self._op_logger.get_records()
        records = [r for r in all_records if r["operation_id"] in operation_ids and r.get("success")]
        return self._do_rollback(records, dry_run)

    def _do_rollback(self, records: list, dry_run: bool) -> List[dict]:
        if not records:
            logger.info("没有可回滚的操作")
            return []

        results = []
        for record in records:
            result = self._rollback_one(record, dry_run)
            results.append(result)

        return results

    def _rollback_one(self, record: dict, dry_run: bool) -> dict:
        operation_id = record["operation_id"]
        original_path = record["original_path"]
        new_path = record["new_path"]

        result = {
            "operation_id": operation_id,
            "original_path": original_path,
            "new_path": new_path,
            "success": False,
            "error": None,
        }

        if dry_run:
            logger.info(f"[模拟回滚] {os.path.basename(new_path)} -> {os.path.basename(original_path)}")
            result["success"] = True
            result["dry_run"] = True
            return result

        if not os.path.exists(new_path):
            error = f"重命名后的文件不存在: {new_path}"
            logger.warning(error)
            result["error"] = error
            return result

        if os.path.exists(original_path):
            error = f"原文件名已存在，无法回滚: {original_path}"
            logger.warning(error)
            result["error"] = error
            return result

        try:
            os.rename(new_path, original_path)
            logger.info(f"回滚成功: {os.path.basename(new_path)} -> {os.path.basename(original_path)}")
            result["success"] = True
            self._op_logger.remove_records([operation_id])
        except OSError as e:
            error = f"回滚失败: {e}"
            logger.error(error)
            result["error"] = error

        return result

    def show_history(self, count: Optional[int] = 10):
        records = self._op_logger.get_successful_renames(count)
        if not records:
            print("没有最近的更名操作记录")
            return

        print(f"\n最近 {len(records)} 条重命名记录:")
        print("-" * 80)
        for r in records:
            print(f"  [{r['timestamp']}] {r['operation_id']}")
            print(f"    原始: {os.path.basename(r['original_path'])}")
            print(f"    重命名: {os.path.basename(r['new_path'])}")
            print(f"    规则: {r['rule']}")
            print()