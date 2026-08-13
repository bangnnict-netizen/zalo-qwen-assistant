"""HTML admin page for self-service Zalo group bindings."""

from __future__ import annotations

import html
import json
from typing import Any


def _status_badge(label: str) -> str:
    if label == "Nội bộ":
        css = "badge-internal"
    elif label == "Khách hàng":
        css = "badge-customer"
    else:
        css = "badge-undeclared"
    return f'<span class="badge {css}">{html.escape(label)}</span>'


def render_group_admin_page(
    *,
    groups: list[dict[str, str]],
    status_by_group: dict[str, str],
    admin_token: str,
    zalo_connected: bool,
) -> str:
    rows_html = ""
    for group in groups:
        group_id = str(group["group_id"])
        name = group.get("name") or group_id
        gid_attr = html.escape(group_id, quote=True)
        name_attr = html.escape(name, quote=True)
        gid_display = html.escape(group_id)
        name_display = html.escape(name)
        status_label = status_by_group.get(group_id, "Chưa khai báo")
        declared = status_label != "Chưa khai báo"
        unbind_attrs = "" if declared else ' disabled title="Nhóm chưa khai báo"'
        rows_html += f"""
        <tr>
          <td>{name_display}</td>
          <td><code>{gid_display}</code></td>
          <td>{_status_badge(status_label)}</td>
          <td class="actions">
            <button type="button" class="btn-bind" data-group-id="{gid_attr}" data-group-name="{name_attr}" data-action="internal">Là nhóm nội bộ</button>
            <button type="button" class="btn-bind" data-group-id="{gid_attr}" data-group-name="{name_attr}" data-action="customer">Là nhóm khách hàng</button>
            <button type="button" class="btn-bind danger btn-unbind" data-group-id="{gid_attr}" data-group-name="{name_attr}" data-action="unbind"{unbind_attrs}>Gỡ khai báo</button>
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
    button:disabled {{ opacity: 0.45; cursor: not-allowed; }}
    button.danger {{ background: #fee; border: 1px solid #f99; }}
    .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 0.25rem; font-size: 0.9rem; font-weight: 600; }}
    .badge-internal {{ background: #d4edda; color: #155724; }}
    .badge-customer {{ background: #cce5ff; color: #004085; }}
    .badge-undeclared {{ background: #e9ecef; color: #495057; }}
    #toast {{ margin-top: 1rem; min-height: 1.2rem; font-weight: bold; }}
    #toast.success {{ color: #155724; }}
    #toast.error {{ color: #721c24; }}
    #toast.pending {{ color: #555; }}
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

    function showToast(message, kind) {{
      const toast = document.getElementById("toast");
      toast.textContent = message;
      toast.className = kind || "";
    }}

    function restoreRowButtons(row) {{
      row.querySelectorAll(".btn-bind").forEach((btn) => {{
        btn.disabled = btn.classList.contains("btn-unbind") && btn.hasAttribute("data-was-disabled");
        if (btn.dataset.label) {{
          btn.textContent = btn.dataset.label;
        }}
      }});
    }}

    async function bindGroup(btn) {{
      const groupId = String(btn.dataset.groupId || "");
      const name = String(btn.dataset.groupName || groupId);
      const action = btn.dataset.action;
      const groupType = action === "unbind" ? null : action;
      const row = btn.closest("tr");
      const buttons = row.querySelectorAll(".btn-bind");

      buttons.forEach((b) => {{
        if (!b.dataset.label) {{
          b.dataset.label = b.textContent;
        }}
        if (b.classList.contains("btn-unbind") && b.disabled) {{
          b.setAttribute("data-was-disabled", "1");
        }}
        b.disabled = true;
      }});
      const prevLabel = btn.dataset.label || btn.textContent;
      btn.textContent = "Đang lưu...";
      showToast("Đang lưu...", "pending");

      try {{
        const res = await fetch("/zalo/bindgroup", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-Admin-Token": ADMIN_TOKEN,
          }},
          body: JSON.stringify({{ group_id: groupId, name: name, group_type: groupType }}),
        }});
        let detail = "";
        try {{
          const body = await res.json();
          detail = body.detail ? String(body.detail) : "";
        }} catch (_err) {{
          detail = "";
        }}
        if (!res.ok) {{
          showToast("Lỗi: " + (detail || res.status), "error");
          restoreRowButtons(row);
          btn.textContent = prevLabel;
          return;
        }}
        if (groupType === null) {{
          showToast("Đã gỡ khai báo " + name, "success");
        }} else {{
          const kind = groupType === "internal" ? "nội bộ" : "khách hàng";
          showToast("Đã khai báo " + name + " là nhóm " + kind, "success");
        }}
        setTimeout(() => window.location.reload(), 600);
      }} catch (err) {{
        showToast("Lỗi: " + (err && err.message ? err.message : "Không kết nối được server"), "error");
        restoreRowButtons(row);
        btn.textContent = prevLabel;
      }}
    }}

    document.querySelectorAll(".btn-bind").forEach((btn) => {{
      btn.addEventListener("click", () => bindGroup(btn));
    }});
  </script>
</body>
</html>"""


def build_status_map(
    groups: list[dict[str, str]],
    status_resolver: Any,
) -> dict[str, str]:
    return {
        str(group["group_id"]): status_resolver(str(group["group_id"]))
        for group in groups
    }
