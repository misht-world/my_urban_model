"""Проверочный режим: прямой расчёт ТЭП по заданному КИТ (публичный API).

Пользователь знает КИТ — хочет увидеть полный набор ТЭП:
площадь квартир, население, потребности в соцобъектах, баланс территории,
статусы нарушений нормативов.

    >>> from urban_model.modes.verify import verify_kit
    >>> from urban_model.models import Site, CalculationOptions
    >>> from urban_model.normatives import load_normatives
    >>> norms = load_normatives("spb")
    >>> res = verify_kit(1.8, Site(area_m2=30_000), CalculationOptions(floors=12), norms)
    >>> print(res.summary())
"""

from __future__ import annotations

from urban_model.core.forward import compute_tep_for_kit
from urban_model.models.options import CalculationOptions
from urban_model.models.result import TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives


def verify_kit(
    kit: float,
    site: Site,
    options: CalculationOptions | None = None,
    norms: Normatives | None = None,
) -> TEPResult:
    """Рассчитать ТЭП для заданного КИТ и вернуть полный TEPResult.

    Не занимается подбором — просто прогоняет полный прямой расчёт.
    Удобен для проверки «а что будет при КИТ=1.8?».

    Args:
        kit:     Коэффициент использования территории (КИТ).
        site:    Описание квартала.
        options: Параметры расчёта; если None — используются значения по умолчанию.
        norms:   Нормативная база; если None — загружается профиль "spb".

    Returns:
        TEPResult со всеми показателями, статусами, источниками и формулами.
    """
    if norms is None:
        from urban_model.normatives import load_normatives
        norms = load_normatives("spb")
    if options is None:
        options = CalculationOptions()
    return compute_tep_for_kit(kit, site, options, norms)
