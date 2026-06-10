from __future__ import annotations

import sqlite3
from pathlib import Path

from jinja2 import Template
from openpyxl import Workbook
from openpyxl.styles import Font

REPORT_COLUMNS = [
    "source",
    "title",
    "authority",
    "published_at",
    "deadline_at",
    "matched_keywords",
    "url",
    "first_seen_at",
    "last_seen_at",
]

HTML_TEMPLATE = Template(
    """
<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <title>Monitoring veřejných zakázek</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; color: #222; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 0.5rem; vertical-align: top; }
    th { background: #f4f4f4; text-align: left; }
    tr:nth-child(even) { background: #fafafa; }
    .meta { color: #666; margin-bottom: 1rem; }
  </style>
</head>
<body>
  <h1>Monitoring veřejných zakázek</h1>
  <p class="meta">Počet nalezených zakázek: {{ rows|length }}</p>
  <table>
    <thead>
      <tr>
        {% for column in columns %}<th>{{ column }}</th>{% endfor %}
      </tr>
    </thead>
    <tbody>
      {% for row in rows %}
      <tr>
        {% for column in columns %}
          {% if column == "url" %}
          <td><a href="{{ row[column] }}">{{ row[column] }}</a></td>
          {% else %}
          <td>{{ row[column] or "" }}</td>
          {% endif %}
        {% endfor %}
      </tr>
      {% endfor %}
    </tbody>
  </table>
</body>
</html>
"""
)


def export_excel(rows: list[sqlite3.Row], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Zakázky"
    worksheet.append(REPORT_COLUMNS)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        worksheet.append([row[column] for column in REPORT_COLUMNS])
    for column_cells in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        worksheet.column_dimensions[column_cells[0].column_letter].width = min(max_length + 2, 80)
    workbook.save(output_path)
    return output_path


def export_html(rows: list[sqlite3.Row], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        HTML_TEMPLATE.render(rows=rows, columns=REPORT_COLUMNS),
        encoding="utf-8",
    )
    return output_path
