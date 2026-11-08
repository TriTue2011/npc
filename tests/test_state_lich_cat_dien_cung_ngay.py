"""State sensor «Lịch cắt điện» liệt kê mọi đợt cắt trong cùng ngày với
đợt gần nhất (2026.11.8).

- 1 đợt/ngày -> state giữ nguyên dạng cũ.
- Nhiều đợt cùng ngày -> ngày ghi một lần, các khung giờ nối bằng «, ».
- Đợt ở ngày khác không vào state (vẫn nằm trong thuộc tính Tương Lai).

Test dùng dữ liệu mock (ngày/giờ giả, không mã khách hàng, không tên
khách hàng thật). Module sensor.py thật được load với các module
homeassistant được stub (máy này không cài Home Assistant).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime

NPC_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "npc"


def _load_sensor_module():
    """Load sensor.py thật, stub các module homeassistant chưa cài."""
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    ha_components = types.ModuleType("homeassistant.components")
    ha_components.__path__ = []
    ha_sensor = types.ModuleType("homeassistant.components.sensor")

    class SensorEntity:
        pass

    ha_sensor.SensorEntity = SensorEntity
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.STATE_UNKNOWN = "unknown"
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers.__path__ = []
    ha_upd = types.ModuleType("homeassistant.helpers.update_coordinator")

    class CoordinatorEntity:
        def __init__(self, coordinator, *args, **kwargs):
            self.coordinator = coordinator

    ha_upd.CoordinatorEntity = CoordinatorEntity
    ha_util = types.ModuleType("homeassistant.util")
    ha_util.__path__ = []
    ha_dt = types.ModuleType("homeassistant.util.dt")
    # «Hôm nay» cố định 08-11-2026 để các đợt 13-11-2026 đều là tương lai.
    ha_dt.now = lambda tz=None: datetime(2026, 11, 8, 8, 0, 0)

    for name, mod in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.sensor": ha_sensor,
        "homeassistant.const": ha_const,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_upd,
        "homeassistant.util": ha_util,
        "homeassistant.util.dt": ha_dt,
    }.items():
        sys.modules[name] = mod

    pkg = types.ModuleType("npc_test_pkg")
    pkg.__path__ = [str(NPC_DIR)]
    sys.modules["npc_test_pkg"] = pkg
    spec = importlib.util.spec_from_file_location(
        "npc_test_pkg.sensor", NPC_DIR / "sensor.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["npc_test_pkg.sensor"] = module
    spec.loader.exec_module(module)
    return module


S = _load_sensor_module()


def _su_kien(ngay, tu, den, khu_vuc="Khu vực giả", ly_do="Lý do giả"):
    return {
        "Ngày": ngay,
        "Thời gian từ": tu,
        "Thời gian đến": den,
        "Khu vực": khu_vuc,
        "Lý do": ly_do,
    }


class DinhDangStateTest(unittest.TestCase):
    def test_mot_dot_giu_nguyen_dang_cu(self):
        su_kien = [_su_kien("13-11-2026", "06:00", "08:00")]
        self.assertEqual(
            S._dinh_dang_state_lich_cat_dien(su_kien),
            "Có (13-11-2026 06:00 - 08:00)",
        )

    def test_hai_dot_cung_ngay_liet_ke_ca_hai(self):
        # Đưa vào ngược thứ tự để kiểm tra việc sắp xếp vẫn đúng.
        su_kien = [
            _su_kien("13-11-2026", "15:00", "17:00"),
            _su_kien("13-11-2026", "06:00", "08:00"),
        ]
        su_kien.sort(key=S._khoa_sap_xep_lich)
        self.assertEqual(
            S._dinh_dang_state_lich_cat_dien(su_kien),
            "Có (13-11-2026 06:00 - 08:00, 15:00 - 17:00)",
        )

    def test_ba_dot_cung_ngay_liet_ke_du_ba(self):
        su_kien = [
            _su_kien("13-11-2026", "15:00", "17:00"),
            _su_kien("13-11-2026", "20:00", "21:30"),
            _su_kien("13-11-2026", "06:00", "08:00"),
        ]
        su_kien.sort(key=S._khoa_sap_xep_lich)
        self.assertEqual(
            S._dinh_dang_state_lich_cat_dien(su_kien),
            "Có (13-11-2026 06:00 - 08:00, 15:00 - 17:00, 20:00 - 21:30)",
        )

    def test_dot_ngay_khac_khong_vao_state(self):
        su_kien = [
            _su_kien("14-11-2026", "06:00", "08:00"),
            _su_kien("13-11-2026", "15:00", "17:00"),
            _su_kien("13-11-2026", "06:00", "08:00"),
        ]
        su_kien.sort(key=S._khoa_sap_xep_lich)
        self.assertEqual(
            S._dinh_dang_state_lich_cat_dien(su_kien),
            "Có (13-11-2026 06:00 - 08:00, 15:00 - 17:00)",
        )

    def test_dot_thieu_gio_thi_bo_qua_khong_vo_state(self):
        su_kien = [
            _su_kien("13-11-2026", "06:00", "08:00"),
            _su_kien("13-11-2026", "", ""),
        ]
        su_kien.sort(key=S._khoa_sap_xep_lich)
        self.assertEqual(
            S._dinh_dang_state_lich_cat_dien(su_kien),
            "Có (13-11-2026 06:00 - 08:00)",
        )

    def test_khong_co_dot_nao(self):
        self.assertEqual(
            S._dinh_dang_state_lich_cat_dien([]),
            "Không có lịch cắt điện",
        )


class CamBienTichHopTest(unittest.TestCase):
    """Đi qua đúng nhánh lich_cat_dien của EVNSensor.state."""

    def test_state_liet_ke_hai_dot_cung_ngay_va_thuoc_tinh_khong_doi(self):
        su_kien = [
            _su_kien("13-11-2026", "15:00", "17:00"),
            _su_kien("14-11-2026", "06:00", "08:00"),
            _su_kien("13-11-2026", "06:00", "08:00"),
        ]
        hass_gia = types.SimpleNamespace(data={})
        coordinator_gia = types.SimpleNamespace(hass=hass_gia)
        sensor = S.EVNSensor(
            coordinator_gia, "MA_GIA", "lich_cat_dien", "MA_GIA_lich_cat_dien"
        )
        goc = S.laylichcatdien
        try:
            S.laylichcatdien = lambda _ma: list(su_kien)
            state = sensor.state
        finally:
            S.laylichcatdien = goc
        self.assertEqual(
            state, "Có (13-11-2026 06:00 - 08:00, 15:00 - 17:00)"
        )
        thuoc_tinh = sensor.extra_state_attributes
        # Tương Lai vẫn đủ 3 đợt (không đổi); các thuộc tính lẻ vẫn của
        # đợt sớm nhất như bản 2026.11.7.
        self.assertEqual(len(thuoc_tinh["Tương Lai"]), 3)
        self.assertEqual(thuoc_tinh["Ngày"], "13-11-2026")
        self.assertEqual(thuoc_tinh["Thời gian từ"], "06:00")
        self.assertEqual(thuoc_tinh["Thời gian đến"], "08:00")


if __name__ == "__main__":
    unittest.main()
