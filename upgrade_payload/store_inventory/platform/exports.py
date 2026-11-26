"""Field selection and workbook formatting for inventory Excel exports."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unicodedata

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


class ExportValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


PRODUCT_FIELDS = {
    "id": "商品ID", "barcode": "条形码", "brand": "品牌", "name": "品名", "spec": "规格",
    "category": "分类", "location": "存放位置", "unit": "单位", "opening": "期初库存",
    "stock": "当前库存", "effective_threshold": "安全阈值", "status": "状态",
}
EXPORTS = {
    "products": ("商品档案", PRODUCT_FIELDS),
    "transactions": ("库存流水", {
        "id": "流水ID", "time": "时间", "type": "类型", "doc_no": "单据号",
        "product_id": "商品ID", "product_name": "品名", "qty_in": "入库", "qty_out": "出库",
        "qty_adjust": "调整", "operator": "经办人", "note": "备注",
    }),
    "purchase": ("采购清单", {key: label for key, label in PRODUCT_FIELDS.items() if key != "barcode"}),
}


def export_definition(kind):
    if kind not in EXPORTS:
        raise ExportValidationError("不支持的导出类型")
    return EXPORTS[kind]


def selected_export_fields(kind, fields):
    _, available = export_definition(kind)
    if fields is None:
        return list(available)
    if not fields:
        raise ExportValidationError("至少选择一个导出字段")
    if len(set(fields)) != len(fields) or any(key not in available for key in fields):
        raise ExportValidationError("导出字段包含重复项或无效项")
    return list(fields)


def character_width(char):
    if char == "\t":
        return 4
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1


def text_lines(value):
    return str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def text_width(value):
    return max(sum(map(character_width, line)) for line in text_lines(value))


def wrapped_lines(value, width):
    for line in text_lines(value):
        used = start = 0
        for end, char in enumerate(line):
            size = character_width(char)
            if used + size > width:
                yield line[start:end]
                start = end
                used = 0
            used += size
        yield line[start:]


class ExcelExportService:
    def __init__(self, *, product_reader, transaction_reader, purchase_reader, output_directory):
        self._product_reader = product_reader
        self._transaction_reader = transaction_reader
        self._purchase_reader = purchase_reader
        self._output_directory = output_directory

    def export(self, kind: str, *, fields=None, q="", type="全部", start="", end="", status="全部",
               category="全部") -> Path:
        title, labels = export_definition(kind)
        keys = selected_export_fields(kind, fields)
        if kind == "products":
            rows = self._product_reader(q=q, status=status, category=category)
        elif kind == "transactions":
            rows = self._transaction_reader(q=q, type=type, start=start, end=end, limit=2000)
        else:
            rows = self._purchase_reader()
        headers = [labels[key] for key in keys]
        data = [[row.get(key, "" if key == "barcode" else None) for key in keys] for row in rows]
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = title

        heading = f"{title}导出表 · {datetime.now():%Y-%m-%d %H:%M}"
        worksheet.append([heading])
        if len(headers) > 1:
            worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        worksheet["A1"].font = Font(name="微软雅黑", size=16, bold=True, color="FFFFFF")
        worksheet["A1"].fill = PatternFill("solid", fgColor="1F4E79")
        worksheet["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        worksheet.row_dimensions[1].height = 38
        worksheet.append(headers)
        for cell in worksheet[2]:
            cell.font = Font(name="微软雅黑", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="4472C4")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        worksheet.row_dimensions[2].height = 22

        widths = []
        for index, header in enumerate(headers):
            size = max(text_width(header), max((text_width(row[index]) for row in data), default=0))
            if len(headers) == 1:
                size = max(size, text_width(heading) + 12)
            width = min(64, max(14, int(size * 1.2) + 5))
            widths.append(width)
            worksheet.column_dimensions[get_column_letter(index + 1)].width = width

        font = Font(name="微软雅黑", size=10)
        alignment = Alignment(vertical="center", wrap_text=True)
        overflow = None
        lines_per_row = 23
        for row_number, row in enumerate(data, start=3):
            max_lines = 1
            for column, value in enumerate(row, start=1):
                cell = worksheet.cell(row=row_number, column=column, value=value)
                if isinstance(value, str):
                    # Business text can start with '=' and must stay literal.
                    cell.data_type = "s"
                cell.font = font
                cell.alignment = alignment
                lines = list(wrapped_lines(value, max(1, int((widths[column - 1] - 4) / 1.2))))
                max_lines = max(max_lines, min(lines_per_row, len(lines)))
                if len(lines) > lines_per_row:
                    if overflow is None:
                        overflow = workbook.create_sheet("长文本续页")
                        overflow.append(["原表单元格", "字段", "段次", "完整内容"])
                        overflow.freeze_panes = "A2"
                        for letter, width in zip("ABCD", (16, 18, 12, 68)):
                            overflow.column_dimensions[letter].width = width
                    first = overflow.max_row + 1
                    cell.comment = Comment(f"完整内容见「长文本续页」第 {first} 行起。", "StoreInventory")
                    for offset in range(0, len(lines), lines_per_row):
                        chunk = lines[offset:offset + lines_per_row]
                        overflow.append([cell.coordinate, headers[column - 1], offset // lines_per_row + 1,
                                         "\n".join(chunk)])
                        overflow.cell(row=overflow.max_row, column=4).data_type = "s"
                        overflow.cell(row=overflow.max_row, column=4).font = font
                        overflow.cell(row=overflow.max_row, column=4).alignment = alignment
                        overflow.row_dimensions[overflow.max_row].height = 17 * len(chunk) + 4
            worksheet.row_dimensions[row_number].height = 17 * max_lines + 4

        worksheet.freeze_panes = "A3"
        worksheet.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{max(worksheet.max_row, 2)}"
        output_dir = Path(self._output_directory(kind))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{title}_{datetime.now():%Y%m%d_%H%M%S_%f}.xlsx"
        workbook.save(output_file)
        return output_file
