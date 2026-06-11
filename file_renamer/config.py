import os
import yaml
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class Condition:
    type: str
    pattern: Optional[str] = None
    values: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Condition":
        return cls(
            type=data["type"],
            pattern=data.get("pattern"),
            values=data.get("values"),
        )


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
    ignored_patterns: List[str] = field(default_factory=list)

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
            ignored_patterns=data.get("ignored_patterns", []),
        )

    def get_enabled_rules(self) -> List[Rule]:
        return [r for r in self.rules if r.enabled]