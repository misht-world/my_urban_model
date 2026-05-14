"""Pydantic-схемы для отдельных нормативных значений.

Норматив — это лист дерева. Узлы — обычные dict'ы (пространства имён).
Лист определяется наличием поля `type` ∈ {"scalar", "piecewise", "conditional"}.
Без `source` норматив не принимается — это юридическое требование ТЗ.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _BaseNormative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    sources: dict[str, str] | None = None  # для conditional: разные источники по веткам
    unit: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _require_source(self):
        if not self.source and not self.sources:
            raise ValueError(
                f"Норматив без `source` или `sources` недопустим: {self!r}"
            )
        return self


class ScalarNormative(_BaseNormative):
    type: Literal["scalar"]
    value: Any  # обычно float/int, но допускаем dict (например, {per_m2_apartments: 80})

    def resolve(self, **ctx: Any) -> Any:
        return self.value


class Breakpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    up_to: float = Field(..., description="верхняя граница ступени; .inf для последней")
    value: Any


class PiecewiseNormative(_BaseNormative):
    type: Literal["piecewise"]
    argument: str
    breakpoints: list[Breakpoint]

    @model_validator(mode="after")
    def _check_sorted(self):
        ups = [bp.up_to for bp in self.breakpoints]
        if ups != sorted(ups):
            raise ValueError(f"Breakpoints должны быть отсортированы: {ups}")
        if not math.isinf(ups[-1]):
            # допускаем без .inf — тогда выход за последний up_to → ошибка в resolve
            pass
        return self

    def resolve(self, **ctx: Any) -> Any:
        if self.argument not in ctx:
            raise KeyError(
                f"piecewise '{self.argument}' требует аргумент {self.argument}={ctx}"
            )
        x = ctx[self.argument]
        for bp in self.breakpoints:
            if x <= bp.up_to:
                return bp.value
        raise ValueError(
            f"Значение {self.argument}={x} вне диапазона; "
            f"последняя ступень={self.breakpoints[-1].up_to}"
        )


class ConditionalNormative(_BaseNormative):
    type: Literal["conditional"]
    argument: str
    cases: dict[str, Any]

    def resolve(self, **ctx: Any) -> Any:
        if self.argument not in ctx:
            raise KeyError(
                f"conditional '{self.argument}' требует аргумент: {ctx}"
            )
        key = ctx[self.argument]
        # допустимы ключи bool/str/int — приводим к str для поиска
        for k_form in (key, str(key), str(key).lower()):
            if k_form in self.cases:
                return self.cases[k_form]
        raise KeyError(
            f"conditional: нет ветки '{key}' в cases={list(self.cases)}"
        )


class ListNormative(_BaseNormative):
    """Нормативное перечисление допустимых значений (без аргумента контекста).

    Пример — список типовых вместимостей: [90, 100, …, 350].
    Resolve возвращает список целиком; вызывающий код сам решает, как с ним работать
    (проверка вхождения, ближайшие значения и т. п.).
    """
    type: Literal["list"]
    values: list[Any]

    def resolve(self, **ctx: Any) -> list[Any]:
        return self.values


NormativeValue = Annotated[
    Union[ScalarNormative, PiecewiseNormative, ConditionalNormative, ListNormative],
    Field(discriminator="type"),
]

_TYPE_TO_CLASS = {
    "scalar": ScalarNormative,
    "piecewise": PiecewiseNormative,
    "conditional": ConditionalNormative,
    "list": ListNormative,
}


def parse_leaf(raw: dict) -> _BaseNormative:
    """Преобразовать сырой dict с полем `type` в типизированный норматив."""
    t = raw.get("type")
    cls = _TYPE_TO_CLASS.get(t)
    if cls is None:
        raise ValueError(f"Неизвестный type норматива: {t!r}; raw={raw}")
    return cls.model_validate(raw)


def is_leaf(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("type") in _TYPE_TO_CLASS
