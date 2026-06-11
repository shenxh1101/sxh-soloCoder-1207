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
        all_records = self._op_logger._collect_all_records(None, None)
        records = [r for r in all_records if r["operation_id"] in operation_ids and r.get("success") and not r.get("rolled_back")]
        records.sort(key=lambda r: r.get("timestamp", ""))
        return self._do_rollback(records, dry_run)

    def _do_rollback(self, records: list, dry_run: bool) -> List[dict]:
        if not records:
            logger.info("没有可回滚的操作")
            return []

        reversed_records = list(reversed(records))
        logger.info(f"回滚 {len(reversed_records)} 条记录 (从最新到最旧)")

        results = []
        rolled_back_ids = []
        for record in reversed_records:
            result = self._rollback_one(record, dry_run)
            results.append(result)
            if result.get("success") and not result.get("dry_run"):
                rolled_back_ids.append(record["operation_id"])
            if not result.get("success") and not result.get("dry_run"):
                logger.warning(f"回滚中断: {result.get('error')}")
                break

        if rolled_back_ids and not dry_run:
            self._op_logger.mark_rolled_back(rolled_back_ids)

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
            filename_only = os.path.basename(new_path)
            dirname = os.path.dirname(new_path)
            if os.path.exists(os.path.join(dirname, filename_only)):
                new_path = os.path.join(dirname, filename_only)
            else:
                candidates = [
                    os.path.join(dirname, f) for f in os.listdir(dirname)
                    if os.path.isfile(os.path.join(dirname, f))
                ]
                found = False
                for c in candidates:
                    c_basename = os.path.basename(c)
                    if c_basename == os.path.basename(original_path):
                        result["success"] = True
                        result["note"] = "文件已经是原名，跳过"
                        logger.info(f"文件已恢复原名，跳过: {c_basename}")
                        return result
                    if os.path.basename(new_path) in c_basename:
                        new_path = c
                        found = True
                        break
                if not found:
                    error = f"重命名后的文件不存在: {new_path}"
                    logger.warning(error)
                    result["error"] = error
                    return result

        if os.path.exists(original_path) and os.path.normpath(original_path) != os.path.normpath(new_path):
            error = f"原文件名已存在，无法回滚: {original_path}"
            logger.warning(error)
            result["error"] = error
            return result

        try:
            os.rename(new_path, original_path)
            logger.info(f"回滚成功: {os.path.basename(new_path)} -> {os.path.basename(original_path)}")
            result["success"] = True
        except OSError as e:
            error = f"回滚失败: {e}"
            logger.error(error)
            result["error"] = error

        return result

    def show_history(self, count: Optional[int] = 10):
        records = self._op_logger.query_records(
            success_only=True,
            include_rolled_back=True,
            limit=count,
        )
        if not records:
            print("没有最近的更名操作记录")
            return

        print(f"\n最近 {len(records)} 条重命名记录:")
        print("-" * 80)
        for r in records:
            status = "✓ 已回滚" if r.get("rolled_back") else "✓"
            print(f"  [{r['timestamp']}] [{status}] {r['operation_id']}")
            print(f"    原始: {os.path.basename(r['original_path'])}")
            print(f"    重命名: {os.path.basename(r['new_path'])}")
            print(f"    规则: {r['rule']}")
            if r.get("watch_label"):
                print(f"    来源: {r['watch_label']}")
            if r.get("rolled_back_at"):
                print(f"    回滚于: {r['rolled_back_at']}")
            print()