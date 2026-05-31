"""Автоматические текстовые выводы для альбома (Фаза 1, §10 ТЗ).

Деловой нейтральный тон, с указанием причин. Везде термин
«экономический запас до земли, финансирования и налогов» вместо «прибыль».
"""
from __future__ import annotations

from urban_model.export.album.risks import HIGH, detect_risks
from urban_model.models.result import TEPResult

DISCLAIMER = (
    "Экономический расчёт предварительный. Стоимость земли, финансирование, "
    "налоги, ТУ, снос и расселение не учитываются, если не заданы отдельно. "
    "Показатели — в условных единицах; это не полная финансовая модель."
)


def economy_verdict(tep: TEPResult) -> str:
    e = tep.economy
    if e is None:
        return "Экономика не рассчитывалась для этого варианта."
    if e.profit < 0:
        return ("Вариант не формирует положительный экономический запас до "
                "земли, финансирования и налогов. Требуется пересмотреть "
                "плотность, класс жилья, парковочную схему или социальную "
                "нагрузку.")
    if e.margin < 0.03:
        return ("Экономический результат в пограничной зоне. Вариант "
                "чувствителен к изменению себестоимости, цены реализации и "
                "компенсации социальных объектов.")
    return ("Вариант имеет положительный экономический запас до земли, "
            "финансирования и налогов.")


def sellable_verdict(tep: TEPResult) -> str:
    e = tep.economy
    sr = e.sellable_ratio if e is not None else 0.0
    if sr <= 0:
        return ""
    if sr < 0.70:
        return ("Низкий выход продаваемой площади ухудшает экономику варианта. "
                "Стоит проверить планировочную эффективность жилых корпусов.")
    return ("Выход продаваемой площади в приемлемом диапазоне и не является "
            "ключевым ограничением варианта.")


def parking_verdict(tep: TEPResult) -> str:
    total = int(tep.parking_required_places.value or 0)
    if total == 0:
        return ""
    ug = int(tep.parking_underground_places.value or 0)
    op = int(tep.parking_open_places.value or 0)
    if ug / total > 0.50:
        return ("Высокая доля подземных парковок увеличивает себестоимость и "
                "снижает экономический результат варианта.")
    if op / total > 0.70:
        return ("Открытые парковки снижают прямую себестоимость, но занимают "
                "значительную площадь территории.")
    return ("Парковочная схема не создаёт критической нагрузки на экономику и "
            "баланс территории.")


def social_verdict(tep: TEPResult) -> str:
    e = tep.economy
    if e is not None and e.net_social_burden > 0 and e.profit_before_social > 0:
        if e.net_social_burden > 0.15 * e.profit_before_social:
            return ("Социальная инфраструктура — один из ключевых факторов "
                    "снижения экономического результата.")
    kg_acc = tep.kindergarten_places_accepted.value or 0
    kg_req = tep.kindergarten_places_required.value or 0
    if kg_req > 0 and kg_acc == 0:
        return ("Социальная потребность рассчитана, но объект не размещён в "
                "границах территории — требуется проверить допустимость.")
    return ("Социальная нагрузка не является критическим фактором для "
            "экономики варианта.")


def overall_verdict(tep: TEPResult) -> dict:
    """Итоговый вердикт для слайда «Что выбрать / дальше»."""
    e = tep.economy
    risks = detect_risks(tep)
    high = [r for r in risks if r.level == HIGH]
    profit_ok = e is not None and e.profit > 0

    if high:
        status, status_color = "Требует доработки", "#C0392C"
        headline = ("Вариант требует доработки: есть критические факторы "
                    "(см. ниже). Рекомендуется устранить их до проработки.")
    elif profit_ok:
        status, status_color = "Можно прорабатывать", "#15803D"
        headline = ("Вариант может быть принят для дальнейшей проработки при "
                    "условии проверки отмеченных параметров.")
    else:
        status, status_color = "Условно пригоден", "#B87600"
        headline = ("Вариант пригоден для проработки с оговорками — "
                    "экономика в пограничной зоне.")

    pros = []
    if e is not None and e.profit > 0:
        pros.append("Положительный экономический запас (до земли и налогов).")
    if e is not None and e.sellable_ratio >= 0.75:
        pros.append("Высокий выход продаваемой площади.")
    if not high:
        pros.append("Нет критических нарушений нормативов.")
    if not pros:
        pros.append("—")

    cons = [risks[0].title + "."] if risks else ["Существенных минусов не выявлено."]

    checks = []
    if e is not None and e.sellable_ratio < 0.70:
        checks.append("Планировочную эффективность жилых корпусов.")
    total = int(tep.parking_required_places.value or 0)
    ug = int(tep.parking_underground_places.value or 0)
    if total and ug / total > 0.50:
        checks.append("Парковочную схему (высокая доля подземных).")
    if e is not None and e.profit > 0 and e.profit_before_social < 0:
        checks.append("Допустимый уровень компенсации соцобъектов.")
    dens = tep.density_chel_per_ga
    if dens.normative and dens.value and dens.value >= dens.normative - 1.0:
        checks.append("Допустимость принятой плотности.")
    if not checks:
        checks.append("Базовые параметры подтверждены — критичных проверок нет.")

    return {
        "status": status, "status_color": status_color, "headline": headline,
        "pros": pros, "cons": cons, "checks": checks,
    }
