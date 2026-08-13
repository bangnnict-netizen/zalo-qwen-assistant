"""HTML admin page for self-service Zalo group bindings."""

from __future__ import annotations

import html
import json
from typing import Any


def render_group_admin_page(
    *,
    groups: list[dict[str, str]],
    status_by_group: dict[str, str],
    admin_token: str,
    zalo_connected: bool,
) -> str:
    rows_html = ""
    for group in groups:
        gid = html.escape(group["group_id"])
        name = group.get("name") or group["group_id"]
        name_esc = html.escape(name)
        gid_js = json.dumps(group["group_id"])
        name_js = json.dumps(name)
        status = html.escape(status_by_group.get(group["group_id"], "Chưa khai báo"))
        rows_html += f"""
        <tr>
          <td>{name_esc}</td>
          <td><code>{gid}</code></td>
          <td>{status}</td>
          <td class="actions">
            <button onclick="bindGroup({gid_js}, {name_js}, 'internal')">Là nhóm nội bộ</button>
            <button onclick="bindGroup({gid_js}, {name_js}, 'customer')">Là nhóm khách hàng</button>
            <button class="danger" onclick="bindGroup({gid_js}, {name_js}, null)">Gỡ khai báo</button>
          </td>
        </tr>"""

    if not groups:
        rows_html = """
        <tr><td colspan="4">Không có nhóm nào (Zalo chưa kết nối hoặc tài khoản chưa tham gia nhóm).</td></tr>"""

    connection_note = (
        "Zalo đang kết nối."
        if zalo_connected
        else "Zalo chưa kết nối — danh sách nhóm có thể trống."
    )

    token_json = json.dumps(admin_token)

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <title>Quản trị nhóm Zalo</title>
  <style>
    body {{ font-family: sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ margin-bottom: 0.2rem; }}
    .note {{ color: #555; margin-bottom: 1.5rem; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid #ddd; padding: 0.6rem; text-align: left; vertical-align: top; }}
    th {{ background: #f5f5f5; }}
    .actions button {{ margin: 0.15rem 0.2rem 0.15rem 0; }}
    button {{ cursor: pointer; padding: 0.35rem 0.6rem; }}
    button.danger {{ background: #fee; border: 1px solid #f99; }}
    #toast {{ margin-top: 1rem; min-height: 1.2rem; color: green; font-weight: bold; }}
  </style>
</head>
<body>
  <h1>Quản trị nhóm Zalo</h1>
  <p class="note">{html.escape(connection_note)} Chọn loại nhóm để bot lắng nghe và trả lời.</p>
  <table>
    <thead>
      <tr>
        <th>Tên nhóm</th>
        <th>Group ID</th>
        <th>Trạng thái</th>
        <th>Thao tác</th>
      </tr>
    </thead>
    <tbody>{rows_html}
    </tbody>
  </table>
  <div id="toast"></div>
  <script>
    const ADMIN_TOKEN = {token_json};

    async function bindGroup(groupId, name, groupType) {{
      const toast = document.getElementById("toast");
      toast.textContent = "Đang lưu...";
      toast.style.color = "#555";
      const res = await fetch("/zalo/bindgroup", {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "X-Admin-Token": ADMIN_TOKEN,
        }},
        body: JSON.stringify({{ group_id: groupId, name: name, group_type: groupType }}),
      }});
      if (!res.ok) {{
        toast.textContent = "Lỗi: " + res.status;
        toast.style.color = "crimson";
        return;
      }}
      toast.textContent = "Đã cập nhật. Đang tải lại...";
      toast.style.color = "green";
      setTimeout(() => window.location.reload(), 400);
    }}
  </script>
</body>
</html>"""


def build_status_map(
    groups: list[dict[str, str]],
    status_resolver: Any,
) -> dict[str, str]:
    return {group["group_id"]: status_resolver(group["group_id"]) for group in groups}
