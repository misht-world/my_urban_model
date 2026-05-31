"""Движок автоматического определения рисков варианта (Фаза 1).

Чистая логика над TEPResult: по порогам формирует список рисков с уровнем
(низкий / средний / высокий). Покрыто тестами.
"""
from __future__ import annotations

from dataclasses import dataclass

from urban_model.models.result import TEPResult

LOW = "низкий"
MID = "средний"
HIGH = "высокий"

_LEVEL_ORDER = {HIGH: 0, MID: 1, LOW: 2}


@dataclass(frozen=True)
class Risk:
    title: str
    level: str
    note: str


def detect_risks(tep: TEPResult) -> list[Risk]:
    risks: list[Risk] = []
    e = tep.economy
    b = tep.balance

    if e is not None:
        if e.profit < 0:
            risks.append(Risk(
                "Отрицательный экономический запас", HIGH,
                "Вариант не формирует положительный запас до земли, "
                "финансирования и налогов.",
            ))
        elif 0 <= e.margin < 0.03:
            risks.append(Risk(
                "Экономика около нуля", MID,
                "Результат в пограничной зоне — чувствителен к себестоимости, "
                "цене реализации и компенсации соцобъектов.",
            ))
        if e.profit > 0 and e.profit_before_social < 0:
            risks.append(Risk(
                "Зависимость от компенсации соцобъектов", MID,
                "Положительный результат держится за счёт компенсации "
                "социальных объектов городом.",
            ))

    sr = e.sellable_ratio if e is not None else 0.0
    if 0 < sr < 0.65:
        risks.append(Risk(
            "Очень низкий выход продаваемой площади", HIGH,
            f"Выход жилья {sr * 100:.0f}% — планировочная эффективность "
            "корпусов требует пересмотра.",
        ))
    elif 0.65 <= sr < 0.70:
        risks.append(Risk(
            "Низкий выход продаваемой площади", MID,
            f"Выход жилья {sr * 100:.0f}% — пограничное значение.",
        ))

    total_p = int(tep.parking_required_places.value or 0)
    ug = int(tep.parking_underground_places.value or 0)
    if total_p > 0:
        ug_share = ug / total_p
        if ug_share > 0.75:
            risks.append(Risk(
                "Очень высокая доля подземных парковок", HIGH,
                f"{ug_share * 100:.0f}% мест — подземные; резко повышает "
                "себестоимость при низкой реализации.",
            ))
        elif ug_share > 0.50:
            risks.append(Risk(
                "Высокая доля подземных парковок", MID,
                f"{ug_share * 100:.0f}% мест — подземные; повышает "
                "себестоимость варианта.",
            ))

    for label, req_f, acc_f in (
        ("ДОО", tep.kindergarten_places_required, tep.kindergarten_places_accepted),
        ("СОШ", tep.school_places_required, tep.school_places_accepted),
    ):
        req = req_f.value or 0
        acc = acc_f.value or 0
        if req > 0 and acc + 0.5 < req:
            risks.append(Risk(
                f"Дефицит мест {label}", HIGH,
                f"Принято {acc:.0f} из требуемых {req:.0f} мест.",
            ))

    from urban_model.calculations.warning_codes import WC, has_code
    for w in tep.warnings:
        if has_code(w, WC.SOC_CAP_MIN_BELOW, WC.SOC_CAP_MAX_ABOVE):
            risks.append(Risk(
                "Вместимость соцобъекта вне норматива", HIGH,
                "Расчётная вместимость ДОО/СОШ выходит за нормативные границы "
                "— требуется иное размещение.",
            ))
            break

    dens = tep.density_chel_per_ga
    if dens.normative and dens.value is not None and dens.value >= dens.normative - 1.0:
        risks.append(Risk(
            "Плотность на нормативном потолке", MID,
            f"Плотность {dens.value:.0f} чел/га достигла норматива "
            f"{dens.normative:.0f} — резерва по населению нет.",
        ))

    site = b.site_area or 0.0
    if site > 0 and b.surplus < site * 0.01:
        risks.append(Risk(
            "Нет резерва территории", LOW,
            "Свободной территории практически не осталось.",
        ))

    risks.sort(key=lambda r: _LEVEL_ORDER.get(r.level, 9))
    return risks


def risk_level_color(level: str) -> str:
    return {HIGH: "#C0392C", MID: "#B87600", LOW: "#8A8A8A"}.get(level, "#8A8A8A")
