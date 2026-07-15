"""Финансирование объектов в экономике (v0.19.0).

До v0.19 финансирование было ОДНИМ глобальным переключателем на все
соцобъекты (`CalculationOptions.social_funding`), а спортплощадки,
инженерия, парковки соцобъектов и пользовательские объекты вообще ему не
подчинялись — всегда за счёт застройщика.

Теперь у каждого объекта свой режим. Глобальный переключатель остаётся
значением по умолчанию (режим `default`) — старые проекты считаются как
раньше.

ВАЖНО (смена семантики v0.19.0): режим «только потребность» (`only_demand`)
БОЛЬШЕ НЕ обнуляет экономику. Он означает ровно одно — объект не занимает
территорию квартала (строится за его пределами). Платит за него застройщик
или нет — решает режим финансирования. Раньше `only_demand` молча делал
объект бесплатным, что скрывало реальные затраты.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Ключи объектов с настраиваемым финансированием.
FUNDING_KEYS: tuple[str, ...] = (
    "kindergarten",
    "school",
    "add_education",
    "polyclinic",
    "sport",
    "social_parking",
    "engineering",
)

# Человекочитаемые названия — для UI и отчётов.
FUNDING_LABELS: dict[str, str] = {
    "kindergarten": "ДОО (детские сады)",
    "school": "СОШ (школы)",
    "add_education": "Доп. образование",
    "polyclinic": "Поликлиника",
    "sport": "Спортплощадки",
    "social_parking": "Парковки соцобъектов",
    "engineering": "Инженерная инфраструктура",
}

# Объекты, которые по умолчанию следуют ГЛОБАЛЬНОМУ режиму (соцобъекты НГП).
# Остальные (спорт, соц-парковки, инженерия, пользовательские объекты) по
# умолчанию — за счёт застройщика: так было до v0.19, поведение сохраняется.
_FOLLOW_GLOBAL: frozenset[str] = frozenset(
    {"kindergarten", "school", "add_education", "polyclinic"}
)

# Режимы (эффективные, после разрешения `default`).
EffectiveMode = Literal["developer", "compensated", "not_developer"]


class ObjectFunding(BaseModel):
    """Режим финансирования одного объекта.

    mode:
      default        — как в общей настройке карточки «Экономика»
                       (для спорта/соц-парковок/инженерии/польз. объектов
                       это «застройщик»);
      developer      — за счёт застройщика, компенсации нет;
      compensated    — за счёт застройщика, город компенсирует долю;
      not_developer  — не за счёт застройщика (строит город, другой инвестор
                       или объект уже существует): затрат и выручки нет.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["default", "developer", "compensated", "not_developer"] = "default"
    compensation_share: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Доля компенсации для mode='compensated'. None → общая настройка/норматив",
    )


def resolve_funding(options, key: str, norms=None) -> tuple[str, float]:
    """Эффективный режим объекта → (mode, comp_share).

    Args:
        options: CalculationOptions.
        key: ключ из FUNDING_KEYS либо ObjectFunding пользовательского объекта.
        norms: нормативы (для доли компенсации по умолчанию).

    Returns:
        (mode, comp_share): mode ∈ {"developer", "compensated", "not_developer"};
        comp_share — доля компенсации (0.0 для developer/not_developer).
    """
    spec = (getattr(options, "object_funding", None) or {}).get(key)
    return resolve_funding_spec(spec, options, norms, follow_global=key in _FOLLOW_GLOBAL)


def resolve_funding_spec(
    spec: ObjectFunding | None, options, norms=None, *, follow_global: bool = False,
) -> tuple[str, float]:
    """Разрешить ObjectFunding (в т.ч. у пользовательского объекта).

    follow_global=True → режим `default` берётся из глобальной настройки
    `social_funding`; иначе `default` = «застройщик».
    """
    mode = spec.mode if spec is not None else "default"
    share_override = spec.compensation_share if spec is not None else None

    if mode == "default":
        if not follow_global:
            return "developer", 0.0
        mode, share = _from_global(options, norms)
        if share_override is not None and mode == "compensated":
            share = float(share_override)
        return mode, share

    if mode == "compensated":
        if share_override is not None:
            return "compensated", float(share_override)
        # Доля не задана на объекте → общая настройка карточки, иначе норматив.
        _user = getattr(options, "social_compensation_share", None)
        if _user is not None:
            return "compensated", float(_user)
        return "compensated", _norm_share(norms)

    # developer / not_developer — доли компенсации нет.
    return mode, 0.0


def _norm_share(norms) -> float:
    if norms is None:
        return 0.0
    try:
        return float(norms.resolve("economy.social_compensation.share"))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _from_global(options, norms) -> tuple[str, float]:
    """Глобальный `social_funding` → (эффективный режим, доля компенсации)."""
    g = getattr(options, "social_funding", "compensated")
    if g == "developer":
        return "developer", 0.0
    if g == "city":
        # Строит город → у застройщика ни затрат, ни компенсации.
        return "not_developer", 0.0
    if g == "at_cost":
        # Передача по себестоимости = компенсация 100%.
        return "compensated", 1.0
    _user = getattr(options, "social_compensation_share", None)
    share = float(_user) if _user is not None else _norm_share(norms)
    return "compensated", share
