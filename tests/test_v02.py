"""Тесты v0.2: verify_kit, compare_scenarios, run_scenarios, xlsx-экспорт, Scenario."""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from urban_model import compare_scenarios, run_scenarios, solve_max_kit, verify_kit
from urban_model.export import results_to_dataframe, to_xlsx
from urban_model.models import CalculationOptions, Scenario, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture(scope="module")
def site_medium():
    return Site(area_m2=50_000, name="Средний")


# ---------------------------------------------------------------------------
# verify_kit
# ---------------------------------------------------------------------------

class TestVerifyKit:
    def test_returns_tepresult(self, spb, site_medium):
        res = verify_kit(1.8, site_medium, CalculationOptions(floors=12), spb)
        assert res.kit.value == 1.8

    def test_source_present(self, spb, site_medium):
        res = verify_kit(1.5, site_medium, norms=spb)
        assert res.population.source is not None

    def test_status_ok_for_low_kit(self, spb):
        """КИТ=0.5 на большом квартале — плотность в норме."""
        res = verify_kit(0.5, Site(area_m2=100_000), norms=spb)
        assert res.density_chel_per_ga.status.value == "ok"

    def test_status_error_for_excessive_density(self, spb):
        """КИТ=2.4 на 1 га — плотность превышает норму."""
        res = verify_kit(2.4, Site(area_m2=10_000), norms=spb)
        assert res.density_chel_per_ga.status.value == "error"

    def test_defaults_no_norms_arg(self):
        """Если norms не передан — загружает spb автоматически."""
        res = verify_kit(1.0, Site(area_m2=30_000))
        assert res.profile == "spb"

    def test_znop_threshold_at_1_75(self, spb):
        """Порог ЗНОП: КИТ=1.75 (≤1.79) → 3 м²/чел; КИТ=1.8 (>1.79) → 4 м²/чел."""
        res_low = verify_kit(1.75, Site(area_m2=50_000), norms=spb)
        res_high = verify_kit(1.8, Site(area_m2=50_000), norms=spb)
        assert res_low.znop_per_person.value == 3
        assert res_high.znop_per_person.value == 4


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

class TestScenario:
    def test_inverse_default(self):
        sc = Scenario(name="X", site=Site(area_m2=10_000))
        assert sc.mode == "inverse"
        assert sc.kit is None

    def test_verify_needs_kit(self):
        with pytest.raises(ValueError, match="kit обязателен"):
            Scenario(name="X", site=Site(area_m2=10_000), mode="verify")

    def test_verify_ok_with_kit(self):
        sc = Scenario(name="X", site=Site(area_m2=10_000), mode="verify", kit=1.5)
        assert sc.kit == 1.5


# ---------------------------------------------------------------------------
# run_scenarios / compare_scenarios
# ---------------------------------------------------------------------------

class TestRunScenarios:
    @pytest.fixture
    def scenarios(self):
        return [
            Scenario(name="Малый", site=Site(area_m2=20_000)),
            Scenario(
                name="Средний",
                site=Site(area_m2=50_000),
                options=CalculationOptions(floors=12, planning_doc=True),
            ),
            Scenario(
                name="Проверка КИТ=1.5",
                site=Site(area_m2=50_000),
                mode="verify",
                kit=1.5,
            ),
        ]

    def test_run_returns_correct_count(self, scenarios, spb):
        pairs = run_scenarios(scenarios, spb)
        assert len(pairs) == 3

    def test_verify_scenario_has_exact_kit(self, scenarios, spb):
        pairs = run_scenarios(scenarios, spb)
        name, res = pairs[2]
        assert name == "Проверка КИТ=1.5"
        assert res.kit.value == 1.5

    def test_inverse_scenario_finds_kit(self, scenarios, spb):
        pairs = run_scenarios(scenarios, spb)
        _, res = pairs[1]
        assert 0 < res.kit.value <= 2.5

    def test_compare_returns_dataframe(self, scenarios, spb):
        df = compare_scenarios(scenarios, spb)
        assert "Малый" in df.columns
        assert "КИТ" in df.index

    def test_compare_columns_order(self, scenarios, spb):
        df = compare_scenarios(scenarios, spb)
        assert list(df.columns) == ["Малый", "Средний", "Проверка КИТ=1.5"]

    def test_compare_verify_kit_value(self, scenarios, spb):
        df = compare_scenarios(scenarios, spb)
        assert float(df.loc["КИТ", "Проверка КИТ=1.5"]) == 1.5

    def test_no_ppt_max_kit_lower(self, spb):
        site = Site(area_m2=200_000)
        scs = [
            Scenario(name="с ППТ", site=site, options=CalculationOptions(planning_doc=True)),
            Scenario(name="без ППТ", site=site, options=CalculationOptions(planning_doc=False)),
        ]
        pairs = run_scenarios(scs, spb)
        kit_yes = pairs[0][1].kit.value
        kit_no = pairs[1][1].kit.value
        assert kit_no <= 1.4 + 1e-6
        assert kit_yes > kit_no


# ---------------------------------------------------------------------------
# results_to_dataframe
# ---------------------------------------------------------------------------

class TestResultsToDataframe:
    def test_shape(self, spb, site_medium):
        pairs = [
            ("A", solve_max_kit(site_medium, norms=spb)),
            ("B", verify_kit(1.5, site_medium, norms=spb)),
        ]
        df = results_to_dataframe(pairs)
        assert "A" in df.columns
        assert "B" in df.columns
        assert "КИТ" in df.index
        assert "Баланс статус" in df.index


# ---------------------------------------------------------------------------
# xlsx-экспорт
# ---------------------------------------------------------------------------

class TestToXlsx:
    @pytest.fixture
    def pairs(self, spb):
        site = Site(area_m2=50_000)
        return [
            ("Обратный", solve_max_kit(site, norms=spb)),
            ("Проверка 1.8", verify_kit(1.8, site, norms=spb)),
        ]

    def test_creates_file(self, pairs):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "report.xlsx"
            result_path = to_xlsx(pairs, path)
            assert result_path.exists()
            assert result_path.stat().st_size > 0

    def test_returns_path(self, pairs):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "r.xlsx"
            result = to_xlsx(pairs, path)
            assert isinstance(result, pathlib.Path)

    def test_xlsx_has_correct_sheets(self, pairs):
        """Проверяем, что в книге есть листы Сравнение и Аудит."""
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as tmp:
            path = to_xlsx(pairs, pathlib.Path(tmp) / "r.xlsx")
            wb = load_workbook(path)
            assert "Сравнение" in wb.sheetnames
            assert "Аудит" in wb.sheetnames

    def test_comparison_sheet_has_data(self, pairs):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as tmp:
            path = to_xlsx(pairs, pathlib.Path(tmp) / "r.xlsx")
            wb = load_workbook(path)
            ws = wb["Сравнение"]
            # Первая строка — заголовок с именами сценариев
            header_vals = [c.value for c in ws[1]]
            assert "Обратный" in header_vals
            assert "Проверка 1.8" in header_vals
            # Не менее 10 строк данных
            assert ws.max_row >= 10
