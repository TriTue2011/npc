"""HTTP views for EVN integration."""

import json
import logging
import mimetypes
from datetime import datetime
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN, CONF_CUSTOMER_ID

_LOGGER = logging.getLogger(__name__)


class EVNPingView(HomeAssistantView):
    """Simple ping endpoint to verify API is working."""

    url = "/api/npc/ping"
    name = "api:npc:ping"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request):
        """Handle GET request."""
        return web.json_response({
            "status": "ok",
            "message": "NPC API is running"
        })


class EVNStaticView(HomeAssistantView):
    """Serve static files from webui directory."""

    url = "/npc-monitor/{filename:.*}"
    name = "npc_monitor:static"
    requires_auth = False

    def __init__(self, webui_path: str, hass=None):
        """Initialize the static file server.
        
        Args:
            webui_path: Absolute path to the webui directory
            hass: Home Assistant instance for async operations
        """
        self.webui_path = Path(webui_path)
        self.hass = hass
        _LOGGER.info("EVNStaticView initialized with path: %s", self.webui_path)

    async def get(self, request, filename: str):
        """Serve a static file.
        
        Args:
            request: The HTTP request
            filename: Relative path to the file (e.g., "index.html" or "assets/js/main.js")
        """
        # Default to index.html if no filename or directory requested
        if not filename or filename.endswith('/'):
            filename = filename + 'index.html' if filename else 'index.html'

        # Construct full file path
        file_path = self.webui_path / filename
        
        # Security check: ensure the resolved path is within webui_path
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(self.webui_path.resolve())):
                _LOGGER.warning("Attempted path traversal: %s", filename)
                return web.Response(status=403, text="Forbidden")
        except Exception as ex:
            _LOGGER.error("Error resolving path %s: %s", filename, str(ex))
            return web.Response(status=400, text="Bad Request")

        # Check if file exists
        if not file_path.is_file():
            _LOGGER.warning("File not found: %s", file_path)
            return web.Response(status=404, text="Not Found")

        # Determine content type
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"
        
        # Prepare headers
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
        
        # Add charset for text/* and application/javascript
        if content_type.startswith("text/") or content_type == "application/javascript":
            headers["Content-Type"] = f"{content_type}; charset=utf-8"
        else:
            headers["Content-Type"] = content_type

        # Read file asynchronously
        try:
            hass = request.app["hass"]
            content = await hass.async_add_executor_job(
                lambda: file_path.read_bytes()
            )
            
            return web.Response(
                body=content,
                headers=headers
            )
        except Exception as ex:
            _LOGGER.error("Error reading file %s: %s", file_path, str(ex))
            return web.Response(status=500, text="Internal Server Error")


class EVNOptionsView(HomeAssistantView):
    """View to return available accounts."""

    url = "/api/npc/options"
    name = "api:npc:options"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request):
        """Get list of configured accounts."""
        try:
            hass = request.app["hass"]
            
            # Get all config entries for this domain
            entries = hass.config_entries.async_entries(DOMAIN)
            
            accounts = []
            for entry in entries:
                customer_id = entry.data.get(CONF_CUSTOMER_ID)
                if customer_id:
                    accounts.append({
                        "userevn": customer_id,
                        "id": customer_id,
                        "name": f"EVN {customer_id}",
                        "customer_id": customer_id
                    })
            
            _LOGGER.info("EVNOptionsView returning accounts: %s", accounts)
            
            # Return in format expected by WebUI
            return web.json_response({
                "accounts_json": json.dumps(accounts)
            })
        except Exception as ex:
            _LOGGER.error("Error in EVNOptionsView: %s", str(ex), exc_info=True)
            return web.json_response({"error": str(ex)}, status=500)


class EVNMonthlyDataView(HomeAssistantView):
    """View to return monthly data for an account."""

    url = "/api/npc/monthly/{account}"
    name = "api:npc:monthly"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request, account):
        """Get monthly data for account."""
        try:
            # Get data from database using utils
            from .utils import layhoadon
            
            # Get bills for current year
            current_year = datetime.now().year
            bills = layhoadon(account, current_year)
            
            # Format data for webui
            # layhoadon returns list of tuples: (thang, tien_dien, san_luong_kwh)
            # WebUI expects format: SanLuong and TienDien are arrays of objects with {Tháng, Năm, ...}
            monthly_data = {
                "SanLuong": [],
                "TienDien": []
            }
            
            for bill in bills:
                try:
                    thang = bill[0] if isinstance(bill, tuple) else bill.get("thang", 0)
                    tien_dien = bill[1] if isinstance(bill, tuple) else bill.get("tien_dien", 0)
                    san_luong = bill[2] if isinstance(bill, tuple) else bill.get("san_luong_kwh", 0)
                    
                    # Safely convert to int/float
                    try:
                        thang_int = int(thang) if thang is not None else 0
                    except (ValueError, TypeError):
                        thang_int = 0
                    try:
                        tien_dien_float = float(tien_dien) if tien_dien is not None else 0
                    except (ValueError, TypeError):
                        tien_dien_float = 0
                    try:
                        san_luong_float = float(san_luong) if san_luong is not None else 0
                    except (ValueError, TypeError):
                        san_luong_float = 0
                    
                    monthly_data["SanLuong"].append({
                        "Tháng": thang_int,
                        "Năm": current_year,
                        "Điện tiêu thụ (KWh)": san_luong_float
                    })
                    monthly_data["TienDien"].append({
                        "Tháng": thang_int,
                        "Năm": current_year,
                        "Tiền Điện": tien_dien_float
                    })
                except Exception as bill_ex:
                    _LOGGER.warning("Error processing bill %s: %s", bill, str(bill_ex))
                    continue
            
            _LOGGER.info("EVNMonthlyDataView returning data for %s: %d months", account, len(monthly_data["SanLuong"]))
            return web.json_response(monthly_data)
            
        except Exception as ex:
            _LOGGER.error("Error getting monthly data for %s: %s", account, str(ex), exc_info=True)
            return web.json_response(
                {"error": str(ex)},
                status=500
            )


class EVNDailyDataView(HomeAssistantView):
    """View to return daily data for an account."""

    url = "/api/npc/daily/{account}"
    name = "api:npc:daily"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request, account):
        """Get daily data for account."""
        try:
            # Get data from database using utils
            from .utils import laykhoangtieuthukynay
            from datetime import timedelta
            
            # Get data from last year to today
            today = datetime.now()
            start_date = today - timedelta(days=365)
            
            # laykhoangtieuthukynay expects format dd/mm/yyyy and converts to dd-mm-yyyy
            # So we pass dd/mm/yyyy format
            rows = laykhoangtieuthukynay(
                account,
                start_date.strftime("%d/%m/%Y"),
                today.strftime("%d/%m/%Y")
            )
            
            # Format data for webui
            formatted_data = []
            for row in rows:
                try:
                    ngay = row[0] if row[0] else ""
                    # Safely convert to float
                    try:
                        chi_so = float(row[1]) if row[1] is not None else 0
                    except (ValueError, TypeError):
                        chi_so = 0
                    try:
                        tieu_thu = float(row[2]) if row[2] is not None else 0
                    except (ValueError, TypeError):
                        tieu_thu = 0
                    
                    # Calculate cost (simplified, you may need to adjust)
                    cost = tieu_thu * 2000  # Rough estimate
                    
                    formatted_data.append({
                        "Ngày": ngay,
                        "Điện tiêu thụ (kWh)": tieu_thu,
                        "Tiền điện (VND)": int(cost),
                        "CHISO": chi_so
                    })
                except Exception as row_ex:
                    _LOGGER.warning("Error processing row %s: %s", row, str(row_ex))
                    continue
            
            _LOGGER.info("EVNDailyDataView returning data for %s: %d days", account, len(formatted_data))
            return web.json_response(formatted_data)
            
        except Exception as ex:
            _LOGGER.error("Error getting daily data for %s: %s", account, str(ex), exc_info=True)
            return web.json_response(
                {"error": str(ex)},
                status=500
            )
