"""Загрузка нормативов из YAML с наследованием регион↔база.

Точки расширения:
- Несколько уровней parent (parent: spb → russia → ...) поддерживаются рекурсивно.
- При слиянии словари объединяются глубоко; листы (узлы с `type`) — целиком
  переопределяются регионом.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from urban_model.normatives.schema import is_leaf, parse_leaf


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "configs"


def _deep_merge(base: dict, override: dict) -> dict:
    """Глубокое слияние: override > base. Лист (норматив с `type`) — атомарен."""
    if is_leaf(base) or is_leaf(override):
        return deepcopy(override)
    out = deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def _load_with_parents(profile: str, config_dir: Path) -> dict:
    path = config_dir / f"{profile}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Нет файла нормативов: {path}")
    with path.open("r", encoding="utf-8") as f:
        data: dict = yaml.safe_load(f) or {}
    parent = data.pop("parent", None)
    if parent:
        base = _load_with_parents(parent, config_dir)
        return _deep_merge(base, data)
    return data


def _walk_and_parse(node: Any) -> Any:
    """Заменить листы (dict с type) на типизированные нормативы in place."""
    if is_leaf(node):
        return parse_leaf(node)
    if isinstance(node, dict):
        return {k: _walk_and_parse(v) for k, v in node.items()}
    return node


class Normatives:
    """Дерево нормативов с резолвером по dotted-path."""

    def __init__(self, profile: str, tree: dict, aliases: dict[str, str] | None = None):
        self.profile = profile
        self._tree = tree
        self.aliases = aliases or {}

    def _follow(self, path: str) -> Any:
        # подстановка алиасов: первый сегмент может быть алиасом → ВРИ-код
        parts = path.split(".")
        if parts and parts[0] in self.aliases:
            parts[0] = self.aliases[parts[0]]
        node: Any = self._tree
        for p in parts:
            if not isinstance(node, dict) or p not in node:
                raise KeyError(f"Нет норматива по пути {path!r} (срез: {p}).")
            node = node[p]
        return node

    def get(self, path: str) -> Any:
        """Сырой узел дерева (для отладки и тестов)."""
        return self._follow(path)

    def resolve(self, path: str, **ctx: Any) -> Any:
        """Получить значение норматива. Для piecewise/conditional — с контекстом."""
        node = self._follow(path)
        if hasattr(node, "resolve"):
            return node.resolve(**ctx)
        if isinstance(node, dict):
            raise ValueError(f"{path!r} — это пространство имён, не норматив.")
        return node

    def source_of(self, path: str, **ctx: Any) -> str | None:
        """Источник, с которого взят норматив. Для conditional — ветка sources."""
        node = self._follow(path)
        sources = getattr(node, "sources", None)
        if sources and getattr(node, "argument", None) in ctx:
            key = str(ctx[node.argument])
            if key in sources:
                return sources[key]
        return getattr(node, "source", None)


def load_normatives(profile: str = "spb", config_dir: Path | None = None) -> Normatives:
    cfg_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    raw = _load_with_parents(profile, cfg_dir)
    aliases = raw.pop("aliases", {}) if isinstance(raw, dict) else {}
    tree = _walk_and_parse(raw)
    return Normatives(profile=profile, tree=tree, aliases=aliases)
