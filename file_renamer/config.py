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
    source_dir: Optional[str] = None

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
            source_dir=data.get("source_dir"),
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
class WatchDir:
    path: str
    label: str = ""
    rules: Optional[List[Rule]] = None
    ignored_patterns: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], global_rules: List[Rule], global_ignored: List[str]) -> "WatchDir":
        path = os.path.expandvars(os.path.expanduser(data["path"]))
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        label = data.get("label", os.path.basename(path))
        rules = [Rule.from_dict(r) for r in data.get("rules", [])] if "rules" in data else None
        ignored = data.get("ignored_patterns", None)

        return cls(
            path=path,
            label=label,
            rules=rules,
            ignored_patterns=ignored,
        )

    def get_rules(self, global_rules: List[Rule]) -> List[Rule]:
        rules = self.rules if self.rules is not None else global_rules
        rules.sort(key=lambda r: r.priority)
        return [r for r in rules if r.enabled]

    def get_ignored_patterns(self, global_ignored: List[str]) -> List[str]:
        return self.ignored_patterns if self.ignored_patterns is not None else global_ignored


@dataclass
class AppConfig:
    watch_dirs: List[WatchDir] = field(default_factory=list)
    global_rules: List[Rule] = field(default_factory=list)
    log_file: str = ""
    history_file: str = ""
    poll_interval: float = 1.0
    debounce_seconds: float = 2.0
    stability_checks: int = 3
    ignored_patterns: List[str] = field(default_factory=list)
    log_daily_rotate: bool = False

    @property
    def watch_dir(self) -> str:
        if self.watch_dirs:
            return self.watch_dirs[0].path
        return ""

    @classmethod
    def from_file(cls, path: str) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        log_file = data.get("log_file", "./rename_operations.log")
        if not os.path.isabs(log_file):
            log_file = os.path.abspath(log_file)

        history_file = data.get("history_file", "./rename_history.json")
        if not os.path.isabs(history_file):
            history_file = os.path.abspath(history_file)

        global_rules = [Rule.from_dict(r) for r in data.get("rules", [])]
        global_rules.sort(key=lambda r: r.priority)
        global_ignored = data.get("ignored_patterns", [])

        watch_dirs = []
        if "watch_dirs" in data:
            for wd_data in data["watch_dirs"]:
                watch_dirs.append(WatchDir.from_dict(wd_data, global_rules, global_ignored))
        elif "watch_dir" in data:
            watch_dir_path = os.path.expandvars(os.path.expanduser(data["watch_dir"]))
            if not os.path.isabs(watch_dir_path):
                watch_dir_path = os.path.abspath(watch_dir_path)
            watch_dirs.append(WatchDir(
                path=watch_dir_path,
                label=os.path.basename(watch_dir_path),
                ignored_patterns=global_ignored,
            ))

        return cls(
            watch_dirs=watch_dirs,
            global_rules=global_rules,
            log_file=log_file,
            history_file=history_file,
            poll_interval=data.get("poll_interval", 1.0),
            debounce_seconds=data.get("debounce_seconds", 2.0),
            stability_checks=data.get("stability_checks", 3),
            ignored_patterns=global_ignored,
            log_daily_rotate=data.get("log_daily_rotate", False),
        )