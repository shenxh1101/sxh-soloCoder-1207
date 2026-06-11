import os
import re
import fnmatch
import logging
import datetime
import time
from typing import List, Optional, Tuple, Dict

from .config import Rule, Condition

logger = logging.getLogger("file_renamer")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".ico"}

COLOR_NAMES_CN = {
    (255, 0, 0): "红色", (255, 69, 0): "橙红", (255, 165, 0): "橙色",
    (255, 215, 0): "金色", (255, 255, 0): "黄色", (173, 255, 47): "黄绿",
    (0, 128, 0): "绿色", (0, 255, 127): "青绿", (0, 255, 255): "青色",
    (0, 191, 255): "深天蓝", (0, 0, 255): "蓝色", (138, 43, 226): "蓝紫",
    (128, 0, 128): "紫色", (255, 0, 255): "品红", (255, 20, 147): "深粉",
    (255, 192, 203): "粉色", (255, 255, 255): "白色", (192, 192, 192): "银色",
    (128, 128, 128): "灰色", (64, 64, 64): "深灰", (0, 0, 0): "黑色",
    (139, 69, 19): "棕色", (160, 82, 45): "褐色", (210, 180, 140): "棕褐",
}

_TIME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_time_offset(offset_str: str) -> float:
    offset_str = offset_str.strip().lower()
    if offset_str == "":
        return 0

    pattern = re.compile(r"(\d+)\s*(s|m|h|d)")
    total = 0.0
    for match in pattern.finditer(offset_str):
        value = int(match.group(1))
        unit = match.group(2)
        total += value * _TIME_UNITS.get(unit, 0)
    return total


def _extract_captures(rule: Rule, filepath: str) -> Dict[str, str]:
    captures = {}
    filename = os.path.basename(filepath)
    dirname = os.path.dirname(filepath)
    dirname_basename = os.path.basename(dirname)

    captures["source_dir"] = dirname_basename
    captures["source_path"] = dirname

    for cond in rule.conditions:
        if cond.type == "filename_regex" and cond.pattern:
            try:
                m = re.search(cond.pattern, filename)
                if m:
                    for i, g in enumerate(m.groups(), 1):
                        captures[str(i)] = g or ""
                    captures.update({k: v or "" for k, v in m.groupdict().items()})
            except re.error:
                pass

    return captures


def _get_dominant_color_name(image_path: str) -> str:
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未安装，无法提取图片主色调")
        return "未知色"

    try:
        img = Image.open(image_path).convert("RGB")
        img = img.resize((50, 50), Image.LANCZOS)
        pixels = list(img.getdata())

        color_counts = {}
        for pixel in pixels:
            r = (pixel[0] // 16) * 16
            g = (pixel[1] // 16) * 16
            b = (pixel[2] // 16) * 16
            quantized = (r, g, b)
            color_counts[quantized] = color_counts.get(quantized, 0) + 1

        if not color_counts:
            return "未知色"

        dominant_rgb = max(color_counts, key=color_counts.get)

        closest_name = "未知色"
        min_distance = float("inf")
        for ref_rgb, name in COLOR_NAMES_CN.items():
            distance = (
                (dominant_rgb[0] - ref_rgb[0]) ** 2
                + (dominant_rgb[1] - ref_rgb[1]) ** 2
                + (dominant_rgb[2] - ref_rgb[2]) ** 2
            )
            if distance < min_distance:
                min_distance = distance
                closest_name = name

        return closest_name
    except Exception as e:
        logger.debug(f"提取主色调失败 {image_path}: {e}")
        return "未知色"


def _check_condition(condition: Condition, filepath: str, filename: str, ext: str) -> bool:
    if condition.type == "extension":
        if condition.values:
            ext_lower = ext.lower()
            allowed = [v.lower() if not v.startswith(".") else v.lower() for v in condition.values]
            allowed_dot = [f".{v}" if not v.startswith(".") else v for v in allowed]
            return ext_lower in allowed_dot
        return False

    elif condition.type == "filename_pattern":
        if condition.pattern:
            return fnmatch.fnmatch(filename, condition.pattern)
        return False

    elif condition.type == "filename_regex":
        if condition.pattern:
            try:
                return bool(re.search(condition.pattern, filename))
            except re.error:
                return False
        return False

    elif condition.type == "is_image":
        return ext.lower() in IMAGE_EXTENSIONS

    elif condition.type == "file_size":
        try:
            stat = os.stat(filepath)
            size = stat.st_size
            if condition.min_size is not None and size < condition.min_size:
                return False
            if condition.max_size is not None and size > condition.max_size:
                return False
            return True
        except OSError:
            return False

    elif condition.type == "created_after":
        try:
            stat = os.stat(filepath)
            ctime = stat.st_ctime
            threshold = time.time() - _parse_time_offset(condition.created_after or "0")
            return ctime >= threshold
        except OSError:
            return False

    elif condition.type == "created_before":
        try:
            stat = os.stat(filepath)
            ctime = stat.st_ctime
            threshold = time.time() - _parse_time_offset(condition.created_before or "0")
            return ctime <= threshold
        except OSError:
            return False

    elif condition.type == "source_dir":
        if condition.source_dir:
            dirname = os.path.dirname(filepath)
            return fnmatch.fnmatch(os.path.basename(dirname), condition.source_dir)
        return False

    return False


def _match_rule(rule: Rule, filepath: str) -> bool:
    if not rule.conditions:
        return True

    filename = os.path.basename(filepath)
    _, ext = os.path.splitext(filename)

    return all(_check_condition(c, filepath, filename, ext) for c in rule.conditions)


def find_matching_rule(filepath: str, rules: List[Rule]) -> Optional[Rule]:
    for rule in rules:
        if _match_rule(rule, filepath):
            logger.debug(f"文件 '{filepath}' 匹配规则 '{rule.name}' (优先级 {rule.priority})")
            return rule
    return None


def preview_file(filepath: str, rules: List[Rule]) -> Optional[dict]:
    if not os.path.isfile(filepath):
        return None

    rule = find_matching_rule(filepath, rules)
    stat = os.stat(filepath)

    if rule is None:
        return {
            "filepath": filepath,
            "filename": os.path.basename(filepath),
            "size": stat.st_size,
            "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
            "matched": False,
            "rule_name": None,
            "rule_priority": None,
            "new_name": None,
            "new_path": None,
            "will_change": False,
            "captures": {},
        }

    new_path = generate_new_name(filepath, rule)
    captures = _extract_captures(rule, filepath)
    return {
        "filepath": filepath,
        "filename": os.path.basename(filepath),
        "size": stat.st_size,
        "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
        "matched": True,
        "rule_name": rule.name,
        "rule_priority": rule.priority,
        "new_name": os.path.basename(new_path),
        "new_path": new_path,
        "will_change": os.path.normpath(new_path) != os.path.normpath(filepath),
        "captures": captures,
    }


def _resolve_templates(pattern: str, filepath: str, rule: Optional[Rule] = None) -> str:
    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    now = datetime.datetime.now()

    result = pattern

    result = result.replace("{name}", name)
    result = result.replace("{ext}", ext)
    result = result.replace("{original_name}", filename)
    result = result.replace("{date}", now.strftime("%Y%m%d"))
    result = result.replace("{time}", now.strftime("%H%M%S"))

    datetime_pattern = re.compile(r"\{datetime:([^}]+)\}")
    for match in datetime_pattern.finditer(result):
        format_str = match.group(1)
        result = result.replace(match.group(0), now.strftime(format_str))

    if "{dominant_color}" in result:
        if ext.lower() in IMAGE_EXTENSIONS:
            color_name = _get_dominant_color_name(filepath)
        else:
            color_name = "未知色"
        result = result.replace("{dominant_color}", color_name)

    if rule:
        captures = _extract_captures(rule, filepath)
        capture_pattern = re.compile(r"\{capture:([^}]+)\}")
        for match in capture_pattern.finditer(result):
            key = match.group(1)
            val = captures.get(key, "")
            result = result.replace(match.group(0), val)

        result = result.replace("{source_dir}", captures.get("source_dir", ""))
        result = result.replace("{source_path}", captures.get("source_path", ""))

    return result


def generate_new_name(filepath: str, rule: Rule) -> str:
    new_filename = _resolve_templates(rule.rename_pattern, filepath, rule)
    dirname = os.path.dirname(filepath)
    new_path = os.path.join(dirname, new_filename)

    if os.path.normpath(new_path) == os.path.normpath(filepath):
        return filepath

    if os.path.exists(new_path):
        name_part, ext_part = os.path.splitext(new_filename)
        counter = 1
        while True:
            alt_name = f"{name_part}_{counter}{ext_part}"
            alt_path = os.path.join(dirname, alt_name)
            if not os.path.exists(alt_path):
                return alt_path
            counter += 1

    return new_path


def execute_rename(filepath: str, rule: Rule) -> Tuple[str, bool, Optional[str]]:
    try:
        new_path = generate_new_name(filepath, rule)
        if os.path.normpath(new_path) == os.path.normpath(filepath):
            logger.debug(f"文件 '{filepath}' 重命名后名称未变化，跳过")
            return filepath, False, "重命名后名称未变化"

        os.rename(filepath, new_path)
        logger.info(f"重命名: '{os.path.basename(filepath)}' -> '{os.path.basename(new_path)}' (规则: {rule.name})")
        return new_path, True, None
    except OSError as e:
        logger.error(f"重命名失败 '{filepath}': {e}")
        return filepath, False, str(e)
    except Exception as e:
        logger.error(f"重命名异常 '{filepath}': {e}")
        return filepath, False, str(e)


def process_file(filepath: str, rules: List[Rule]) -> Optional[Tuple[str, str, Rule]]:
    if not os.path.isfile(filepath):
        return None

    rule = find_matching_rule(filepath, rules)
    if rule is None:
        return None

    new_path, success, error = execute_rename(filepath, rule)
    if success:
        return (filepath, new_path, rule)
    return None