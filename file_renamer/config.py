import os
import yaml
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class Condition:
    type: str
    pattern: Optional[str] = None
    values: Optional[List[str]] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    min_size_str: Optional[str] = None
    max_size_str: Optional[str] = None
    created_after: Optional[str] = None
    created_before: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Condition":
        min_size = data.get("min_size")
        max_size = data.get("max_size")
        if isinstance(min_size, str):
            min_size_str = min_size
            min_size = cls._parse_size_str(min_size)
        else:
            min_size_str = str(min_size) if min_size is not None else None
        if isinstance(max_size, str):
            max_size_str = max_size
            max_size = cls._parse_size_str(max_size)
        else:
            max_size_str = str(max_size) if max_size is not None else None

        return cls(
            type=data["type"],
            pattern=data.get("pattern"),
            values=data.get("values"),
            min_size=min_size,
            max_size=max_size,
            min_size_str=min_size_str,
            max_size_str=max_size_str,
            created_after=data.get("created_after"),
            created_before=data.get("created_before"),
        )

    @staticmethod
    def _parse_size_str(size_str: str) -> int:
        size_str = size_str.strip().upper()
        multipliers = {"B": 1, "K": 1024, "KB": 1024, "M": 1024 ** 2, "MB": 1024 ** 2, "G": 1024 ** 3, "GB": 1024 ** 3}
        for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
            if size_str.endswith(suffix):
                return int(float(size_str[:-len(suffix)]) * mult)
        return int(size_str)


@dataclass
class Rule:
    name: str
    priority: int
    conditions: List[Condition]
    rename_pattern: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Rule":
        conditions = [Condition.from_dict(c) for c in data.get("conditions", [])]
        return cls(
            name=data["name"],
            priority=data.get("priority", 100),
            conditions=conditions,
            rename_pattern=data["rename_pattern"],
            enabled=data.get("enabled", True),
        )


@dataclass
class AppConfig:
    watch_dir: str
    log_file: str
    history_file: str
    rules: List[Rule] = field(default_factory=list)
    poll_interval: float = 1.0
    debounce_seconds: float = 2.0
    stability_checks: int = 3
    ignored_patterns: List[str] = field(default_factory=list)
    log_daily_rotate: bool = False

    @classmethod
    def from_file(cls, path: str) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        watch_dir = os.path.expandvars(os.path.expanduser(data["watch_dir"]))
        if not os.path.isabs(watch_dir):
            watch_dir = os.path.abspath(watch_dir)

        log_file = data.get("log_file", "./rename_operations.log")
        if not os.path.isabs(log_file):
            log_file = os.path.abspath(log_file)

        history_file = data.get("history_file", "./rename_history.json")
        if not os.path.isabs(history_file):
            history_file = os.path.abspath(history_file)

        rules = [Rule.from_dict(r) for r in data.get("rules", [])]
        rules.sort(key=lambda r: r.priority)

        return cls(
            watch_dir=watch_dir,
            log_file=log_file,
            history_file=history_file,
            rules=rules,
            poll_interval=data.get("poll_interval", 1.0),
            debounce_seconds=data.get("debounce_seconds", 2.0),
            stability_checks=data.get("stability_checks", 3),
            ignored_patterns=data.get("ignored_patterns", []),
            log_daily_rotate=data.get("log_daily_rotate", False),
        )

    def get_enabled_rules(self) -> List[Rule]:
        return [r for r in self.rules if r.enabled]