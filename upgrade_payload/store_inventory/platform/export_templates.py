"""Persist personal export templates in the existing settings table."""
from contextlib import closing
import json
from uuid import uuid4

from .exports import ExportValidationError, selected_export_fields


class ExcelExportTemplates:
    def __init__(self, connect):
        self._connect = connect

    @staticmethod
    def _key(user_id, kind):
        return f"excel_export_templates:{user_id}:{kind}"

    @staticmethod
    def _load(con, key):
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else []

    def list(self, user_id, kind):
        with closing(self._connect()) as con:
            return self._load(con, self._key(user_id, kind))

    def save(self, user_id, kind, template_id, *, name, fields, is_default):
        name = name.strip()
        if not name:
            raise ExportValidationError("请输入模板名称")
        fields = selected_export_fields(kind, fields)
        key = self._key(user_id, kind)
        with closing(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            templates = self._load(con, key)
            position = next((i for i, template in enumerate(templates) if template["id"] == template_id), None)
            if template_id and position is None:
                raise ExportValidationError("导出模板不存在", 404)
            if any(t["id"] != template_id and t["name"].casefold() == name.casefold() for t in templates):
                raise ExportValidationError("已存在同名导出模板", 409)
            if is_default:
                for template in templates:
                    template["is_default"] = False
            result = {"id": template_id or uuid4().hex, "name": name, "fields": fields, "is_default": is_default}
            if position is None:
                templates.append(result)
            else:
                templates[position] = result
            self._store(con, key, templates)
            return result

    def delete(self, user_id, kind, template_id):
        key = self._key(user_id, kind)
        with closing(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            templates = self._load(con, key)
            remaining = [template for template in templates if template["id"] != template_id]
            if len(remaining) == len(templates):
                raise ExportValidationError("导出模板不存在", 404)
            self._store(con, key, remaining)

    @staticmethod
    def _store(con, key, templates):
        con.execute(
            """INSERT INTO settings(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, json.dumps(templates, ensure_ascii=False)),
        )
