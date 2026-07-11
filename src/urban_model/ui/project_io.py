"""Сохранение/загрузка проекта (v0.14.0): все параметры формы → JSON.

Два механизма:
  1. Файл проекта (.json) — «Сохранить»/«Загрузить» через браузер. Надёжно
     и на Streamlit Cloud (файл у пользователя на диске).
  2. Локальные пресеты — именованные проекты в `~/.my_urban_model_presets.json`
     (работают локально; на Cloud стираются при редеплое).

Сохраняются СКАЛЯРНЫЕ значения виджетов (checkbox/slider/number/select/radio/
text) из st.session_state. Табличные редакторы (зоны этажности, пользовательские
объекты) в v1 не сохраняются — Streamlit не позволяет программно восстановить
состояние st.data_editor.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import streamlit as st

from urban_model import __version__

_PRESETS_PATH = Path.home() / ".my_urban_model_presets.json"

# Ключи session_state, которые НЕ являются параметрами формы.
_EXCLUDE_KEYS = {
    "scenarios", "applied_options", "applied_label", "applied_vpp_request",
    "last_calc_result", "last_calc_options", "last_calc_site_area",
    "album_variant_select",
}
_EXCLUDE_PREFIXES = ("_", "FormSubmitter", "close_", "proj_")


def snapshot_state() -> dict:
    """Снимок скалярных параметров формы из session_state."""
    data: dict = {}
    for k, v in st.session_state.items():
        if k in _EXCLUDE_KEYS or k.startswith(_EXCLUDE_PREFIXES):
            continue
        if isinstance(v, (bool, int, float, str)):
            data[k] = v
    return {
        "_kind": "my_urban_model_project",
        "_version": __version__,
        "_saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "params": data,
    }


def apply_state(payload: dict) -> int:
    """Записать параметры проекта в session_state (до создания виджетов).

    Возвращает число применённых ключей. Вызывать строго ДО рендера формы,
    затем st.rerun().
    """
    params = payload.get("params", payload)  # поддержка и «голого» словаря
    n = 0
    for k, v in params.items():
        if k.startswith("_") or not isinstance(v, (bool, int, float, str)):
            continue
        st.session_state[k] = v
        n += 1
    return n


# ── Локальные пресеты ──

def _load_presets() -> dict:
    try:
        return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_presets(presets: dict) -> None:
    try:
        _PRESETS_PATH.write_text(
            json.dumps(presets, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        st.warning("Не удалось записать файл пресетов.")


def render_project_bar() -> None:
    """Expander «Проект» вверху «Параметров»: файл + пресеты."""
    with st.expander(":material/folder: Проект — сохранить / загрузить", expanded=False):
        c1, c2 = st.columns(2)

        # 1) Файл проекта
        with c1:
            st.caption("**Файл проекта** — сохраняется у вас на диске.")
            snap = snapshot_state()
            st.download_button(
                ":material/download: Сохранить проект (.json)",
                json.dumps(snap, ensure_ascii=False, indent=1).encode("utf-8"),
                file_name=f"проект_{_dt.date.today().isoformat()}.json",
                mime="application/json",
                use_container_width=True,
                key="proj_download",
            )
            up = st.file_uploader(
                "Загрузить проект", type=["json"], key="proj_upload",
                label_visibility="collapsed",
            )
            if up is not None and st.button(
                    ":material/upload: Применить загруженный проект",
                    use_container_width=True, key="proj_apply"):
                try:
                    payload = json.loads(up.getvalue().decode("utf-8"))
                    n = apply_state(payload)
                    st.toast(f"Проект применён ({n} параметров)", icon="📂")
                    st.rerun()
                except (ValueError, UnicodeDecodeError) as e:
                    st.error(f"Файл не читается как проект: {e}")

        # 2) Локальные пресеты
        with c2:
            st.caption("**Пресеты** — быстрый список на этом компьютере.")
            presets = _load_presets()
            name = st.text_input("Имя пресета", key="proj_preset_name",
                                 placeholder="Квартал А")
            if st.button(":material/save: Сохранить пресет",
                         use_container_width=True, key="proj_preset_save",
                         disabled=not name.strip()):
                presets[name.strip()] = snapshot_state()
                _save_presets(presets)
                st.toast(f"Пресет «{name.strip()}» сохранён", icon="💾")
                st.rerun()
            if presets:
                sel = st.selectbox("Сохранённые пресеты", sorted(presets),
                                   key="proj_preset_sel")
                b1, b2 = st.columns(2)
                if b1.button(":material/upload: Загрузить",
                             use_container_width=True, key="proj_preset_load"):
                    n = apply_state(presets[sel])
                    st.toast(f"Пресет «{sel}» применён ({n} параметров)", icon="📂")
                    st.rerun()
                if b2.button(":material/delete: Удалить",
                             use_container_width=True, key="proj_preset_del"):
                    presets.pop(sel, None)
                    _save_presets(presets)
                    st.rerun()
        st.caption(
            "Таблица пользовательских объектов в файл проекта пока не входит "
            "(ограничение табличных редакторов Streamlit). Зоны этажности "
            "сохраняются (v0.15.11)."
        )
