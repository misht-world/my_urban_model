"""Тесты сохранения/загрузки проекта (v0.14.0)."""

from __future__ import annotations

import streamlit as st

from urban_model.ui.project_io import apply_state, snapshot_state


def _clear_state():
    for k in list(st.session_state.keys()):
        del st.session_state[k]


def test_snapshot_scalars_only():
    _clear_state()
    st.session_state["floors"] = 16
    st.session_state["planning_doc"] = True
    st.session_state["area_input_m2"] = 123456.0
    st.session_state["residential_class"] = "Комфорт"
    st.session_state["_private"] = "нет"
    st.session_state["scenarios"] = [("x", None)]      # не скаляр
    st.session_state["proj_preset_name"] = "служебный"
    snap = snapshot_state()
    p = snap["params"]
    assert p["floors"] == 16
    assert p["planning_doc"] is True
    assert "_private" not in p
    assert "scenarios" not in p
    assert "proj_preset_name" not in p
    assert snap["_kind"] == "my_urban_model_project"


def test_apply_round_trip():
    _clear_state()
    st.session_state["floors"] = 16
    st.session_state["park_preset_ml_levels"] = 5
    snap = snapshot_state()
    _clear_state()
    n = apply_state(snap)
    assert n == 2
    assert st.session_state["floors"] == 16
    assert st.session_state["park_preset_ml_levels"] == 5


def test_apply_bare_dict():
    """Поддержка «голого» словаря параметров (без обёртки)."""
    _clear_state()
    n = apply_state({"floors": 9, "_meta": "skip", "bad": [1, 2]})
    assert n == 1
    assert st.session_state["floors"] == 9


def test_zones_saved_via_fc_zones_json():
    """v0.15.11: зоны этажности сохраняются в проект через fc_zones_json."""
    import json
    _clear_state()
    st.session_state["use_floor_clusters"] = True
    st.session_state["fc_zones_json"] = json.dumps(
        [{"Зона": "А", "Площадь, м²": 100000, "Этажность": 9}],
        ensure_ascii=False)
    snap = snapshot_state()
    assert "fc_zones_json" in snap["params"]
    _clear_state()
    apply_state(snap)
    rows = json.loads(st.session_state["fc_zones_json"])
    assert rows[0]["Этажность"] == 9 and rows[0]["Зона"] == "А"


def test_custom_objects_saved_via_co_objects_json():
    """v0.15.12: доп. объекты сохраняются в проект через co_objects_json."""
    import json
    _clear_state()
    st.session_state["co_objects_json"] = json.dumps(
        [{"name": "ФОК", "plot_area_m2": 5000.0, "vri_code": "5.1.2",
          "floor_area_m2": None}], ensure_ascii=False)
    snap = snapshot_state()
    assert "co_objects_json" in snap["params"]
    _clear_state()
    apply_state(snap)
    rows = json.loads(st.session_state["co_objects_json"])
    assert rows[0]["name"] == "ФОК" and rows[0]["plot_area_m2"] == 5000.0
