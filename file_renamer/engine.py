import os
import re
import fnmatch
import logging
import datetime
from typing import List, Optional, Tuple

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


def _resolve_templates(pattern: str, filepath: str) -> str:
    dirname = os.path.dirname(filepath)
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

    return result


def generate_new_name(filepath: str, rule: Rule) -> str:
    new_filename = _resolve_templates(rule.rename_pattern, filepath)
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