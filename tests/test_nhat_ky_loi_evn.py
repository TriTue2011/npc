"""Lỗi phía máy chủ EVN không được thành «ERROR» dài dòng lặp hàng giờ (log HA 31/10–01/11/2026: 15 giờ liên tiếp, mỗi giờ 3 lệnh 500/HTML), và
không được ghi mã điểm đo của khách vào log."""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import pathlib
import unittest
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

SRC = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "npc" / "npc_api.py"
spec = importlib.util.spec_from_file_location("npc_api_cho_test", SRC)
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

LOGGER = "npc_api_cho_test"
MA_KHACH = "PD1300999999"


class HamGhiLoiTest(unittest.TestCase):
    def setUp(self):
        A._LAN_BAO_CUOI.clear()

    def test_loi_5xx_canh_bao_gon_va_chi_nhac_lai_sau_6_gio(self):
        with mock.patch.object(A.time, "monotonic", return_value=1_000_000.0):
            with self.assertLogs(LOGGER, "WARNING") as cm:
                A._ghi_loi_http("get_hoadon", 500)
                A._ghi_loi_http("get_hoadon", 500)          # cùng endpoint, trong hạn: không nhắc lại
            self.assertEqual(len(cm.records), 1)
            self.assertEqual(cm.records[0].levelno, logging.WARNING)
            self.assertIn("HTTP 500", cm.getMessage() if hasattr(cm, "getMessage") else cm.output[0])
        with mock.patch.object(A.time, "monotonic", return_value=1_000_000.0 + 6 * 3600):
            with self.assertLogs(LOGGER, "WARNING") as cm2:
                A._ghi_loi_http("get_hoadon", 502)
            self.assertEqual(len(cm2.records), 1)

    def test_moi_endpoint_duoc_bao_rieng(self):
        with self.assertLogs(LOGGER, "WARNING") as cm:
            A._ghi_loi_http("get_hoadon", 500)
            A._ghi_loi_http("get_chisothang", 500)
        self.assertEqual(len(cm.records), 2)

    def test_loi_4xx_van_la_error_kem_phan_hoi_cat_ngan(self):
        with self.assertLogs(LOGGER, "ERROR") as cm:
            A._ghi_loi_http("get_hoadon", 403, "x" * 1000)
        self.assertEqual(cm.records[0].levelno, logging.ERROR)
        self.assertLess(len(cm.output[0]), 500)

    def test_evn_tra_html_la_canh_bao_khong_co_traceback(self):
        loi = aiohttp.ContentTypeError(mock.Mock(real_url="u"), (), message="Attempt to decode JSON with unexpected mimetype: text/html")
        with self.assertLogs(LOGGER, "WARNING") as cm:
            A._ghi_ngoai_le("get_chisothang", loi)
        self.assertEqual(cm.records[0].levelno, logging.WARNING)
        self.assertIsNone(cm.records[0].exc_info)
        self.assertIn("HTML", cm.output[0])

    def test_het_gio_va_mat_mang_la_loi_phia_evn(self):
        for loi in (asyncio.TimeoutError(), aiohttp.ClientConnectionError("x")):
            A._LAN_BAO_CUOI.clear()
            with self.assertLogs(LOGGER, "WARNING") as cm:
                A._ghi_ngoai_le("get_hoadon", loi)
            self.assertEqual(cm.records[0].levelno, logging.WARNING)

    def test_loi_bat_ngo_van_la_error_co_traceback(self):
        with self.assertLogs(LOGGER, "ERROR") as cm:
            try:
                raise ValueError("hỏng thật")
            except ValueError as e:
                A._ghi_ngoai_le("get_hoadon", e)
        self.assertEqual(cm.records[0].levelno, logging.ERROR)
        self.assertIsNotNone(cm.records[0].exc_info)


class DauCuoiTest(unittest.IsolatedAsyncioTestCase):
    """Gọi thật get_chisothang tới máy chủ EVN giả."""

    async def _chay(self, handler):
        app = web.Application()
        app.router.add_post("/api/evn/tracuu/chisothang", handler)
        async with TestServer(app) as sv:
            api = A.EVNAPI(None, "HN", "u", "p", MA_KHACH)
            api.base_url = str(sv.make_url("")).rstrip("/")
            api.access_token = "token-gia"
            try:
                return await api.get_chisothang(10, 2026)
            finally:
                await api.close()

    async def test_evn_tra_500_thi_none_va_log_khong_co_ma_diem_do(self):
        A._LAN_BAO_CUOI.clear()

        async def h(_req):
            return web.Response(status=500, text="boom")
        with self.assertLogs(LOGGER, "DEBUG") as cm:
            kq = await self._chay(h)
        self.assertIsNone(kq)
        self.assertTrue(all(r.levelno < logging.ERROR for r in cm.records))
        self.assertNotIn(MA_KHACH, "\n".join(cm.output))

    async def test_evn_tra_trang_html_thi_none_va_khong_co_error(self):
        A._LAN_BAO_CUOI.clear()

        async def h(_req):
            return web.Response(status=200, text="<html>bảo trì</html>", content_type="text/html")
        with self.assertLogs(LOGGER, "DEBUG") as cm:
            kq = await self._chay(h)
        self.assertIsNone(kq)
        self.assertTrue(all(r.levelno < logging.ERROR for r in cm.records))
        self.assertTrue(all(r.exc_info is None for r in cm.records))


if __name__ == "__main__":
    unittest.main()
