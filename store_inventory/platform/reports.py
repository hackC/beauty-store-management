"""Application service for the existing operations report."""
from __future__ import annotations
from datetime import date
import json


class ReportValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class OperationsReportService:
    def __init__(self, connection_factory, product_rows_reader, schedule_row_reader):
        self._connect = connection_factory
        self._product_rows = product_rows_reader
        self._schedule_row = schedule_row_reader

    @staticmethod
    def _validate_period(date_from: str, date_to: str) -> None:
        for value in (date_from, date_to):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ReportValidationError("报表日期必须为 YYYY-MM-DD") from exc
        if date_from and date_to and date_from > date_to:
            raise ReportValidationError("开始日期不能晚于结束日期")

    def get_operations_report(self, date_from: str = "", date_to: str = "") -> dict:
        date_from, date_to = date_from or "", date_to or ""
        self._validate_period(date_from, date_to)
        con = self._connect()
        try:
            tx_sql = ("SELECT type,COUNT(*) AS line_count,COALESCE(SUM(qty_in),0) AS qty_in,"
                      "COALESCE(SUM(qty_out),0) AS qty_out,COALESCE(SUM(qty_adjust),0) AS qty_adjust "
                      "FROM transactions WHERE 1=1")
            tx_args = []
            if date_from:
                tx_sql += " AND date(time)>=date(?)"; tx_args.append(date_from)
            if date_to:
                tx_sql += " AND date(time)<=date(?)"; tx_args.append(date_to)
            tx_sql += " GROUP BY type ORDER BY type"
            transaction_summary = [dict(x) for x in con.execute(tx_sql, tx_args).fetchall()]

            daily_sql = ("SELECT date(time) AS day,COUNT(*) AS line_count,"
                         "COALESCE(SUM(qty_in),0) AS qty_in,COALESCE(SUM(qty_out),0) AS qty_out "
                         "FROM transactions WHERE 1=1")
            if date_from:
                daily_sql += " AND date(time)>=date(?)"
            if date_to:
                daily_sql += " AND date(time)<=date(?)"
            daily_sql += " GROUP BY date(time) ORDER BY day"
            daily_rows = [dict(x) for x in con.execute(daily_sql, tx_args).fetchall()]
            product_count = con.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
            low_stock_count = sum(1 for x in self._product_rows(con) if x["stock"] <= x["effective_threshold"])
            customer_count = con.execute("SELECT COUNT(*) FROM customer_profiles").fetchone()[0]
            customer_sql = "SELECT COUNT(*) FROM customer_profiles WHERE 1=1"
            customer_args = []
            if date_from:
                customer_sql += " AND date(created_at)>=date(?)"; customer_args.append(date_from)
            if date_to:
                customer_sql += " AND date(created_at)<=date(?)"; customer_args.append(date_to)
            new_customers = con.execute(customer_sql, customer_args).fetchone()[0]
            state = self._schedule_row(con)
            schedule_payload = json.loads(state["payload"]) if state else {}
        finally:
            con.close()

        appointment_count = service_minutes = 0
        staff_summary = {}
        staff_names = {
            str(x.get("id")): str(x.get("name") or x.get("id"))
            for x in schedule_payload.get("staff", []) if isinstance(x, dict)
        }
        for appt in schedule_payload.get("appointments", []):
            if not isinstance(appt, dict) or appt.get("status") == "cancelled":
                continue
            day = str(appt.get("date") or "")
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
            appointment_count += 1
            minutes = max(0, int(appt.get("duration", 0) or 0))
            service_minutes += minutes
            sid = str(appt.get("staffId") or "")
            item = staff_summary.setdefault(sid, {
                "staff_id": sid, "staff_name": staff_names.get(sid, sid),
                "appointment_count": 0, "service_minutes": 0,
            })
            item["appointment_count"] += 1
            item["service_minutes"] += minutes

        return {
            "date_from": date_from, "date_to": date_to,
            "inventory": {
                "active_products": product_count, "low_stock_products": low_stock_count,
                "transactions": transaction_summary, "daily": daily_rows,
            },
            "appointments": {
                "count": appointment_count, "service_minutes": service_minutes,
                "staff": sorted(staff_summary.values(), key=lambda x: (-x["service_minutes"], x["staff_name"])),
            },
            "customers": {"total": customer_count, "new_in_period": new_customers},
        }
