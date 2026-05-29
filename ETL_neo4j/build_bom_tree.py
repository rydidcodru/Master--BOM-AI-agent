import json
import math
import re
from pathlib import Path

import pandas as pd


INPUT = Path(r"G:\내 드라이브\10.프로젝트 자료\01. LG\data\통합 개발부품Master Extra v1.1.xlsx")
OUTPUT_DIR = Path(r"C:\Users\Study\Documents\Codex\2026-05-27\files-mentioned-by-the-user-master\outputs")
OUTPUT_HTML = OUTPUT_DIR / "bom_tree_visualization.html"


def clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def bom_level(value):
    text = clean(value)
    match = re.search(r"(\d+)$", text)
    if match:
        return int(match.group(1))
    return max(1, text.count("."))


def safe_number(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def build_tree(rows):
    roots = []
    stack = []
    flat = []

    for index, row in rows.iterrows():
        level = bom_level(row["BOM\nLevel"])
        node = {
            "id": f"n{int(row['No.']) if clean(row['No.']) else index}",
            "no": safe_number(row["No."]),
            "level": level,
            "bomLevel": clean(row["BOM\nLevel"]),
            "partType": clean(row["Part Type"]),
            "baseNo": clean(row["P/No"]),
            "newNo": clean(row["Unnamed: 5"]),
            "name": clean(row["부품명\nClass Desc.(Part Name)"]),
            "qty": safe_number(row["Q'ty"]),
            "supplier": clean(row["양산처\nSupplier"]),
            "classification": clean(row["신규/변경\n부품 대상\nClassification"]),
            "changingPoint": clean(row["변경점\nChanging Point"]),
            "children": [],
        }

        while len(stack) >= level:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
            node["parentId"] = stack[-1]["id"]
        else:
            roots.append(node)
            node["parentId"] = ""
        stack.append(node)
        flat.append(node)

    return {"roots": roots, "flat": flat}


def render_html(tree):
    data_json = json.dumps(tree["roots"], ensure_ascii=False)
    flat_json = json.dumps(
        [
            {k: v for k, v in node.items() if k != "children"}
            for node in tree["flat"]
        ],
        ensure_ascii=False,
    )
    max_level = max((node["level"] for node in tree["flat"]), default=1)
    total_nodes = len(tree["flat"])
    root_count = len(tree["roots"])

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BOM Tree Visualization</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d7dce3;
      --accent: #006f6b;
      --accent-2: #b6402a;
      --soft: #e9f4f2;
      --mark: #fff2a8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, "Malgun Gothic", sans-serif;
      font-size: 14px;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(255, 255, 255, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 16px 22px 14px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto auto auto;
      gap: 10px;
      align-items: center;
    }}
    input, select, button {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      height: 36px;
      padding: 0 10px;
      font: inherit;
    }}
    button {{
      cursor: pointer;
      font-weight: 600;
    }}
    button.primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: white;
    }}
    main {{
      display: grid;
      grid-template-columns: 1fr 320px;
      gap: 18px;
      padding: 18px 22px 30px;
      align-items: start;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric, aside, .tree-wrap {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{
      padding: 12px 14px;
    }}
    .metric .label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }}
    .metric .value {{
      font-size: 22px;
      font-weight: 700;
    }}
    .tree-wrap {{
      padding: 14px 16px;
      overflow: auto;
      min-height: 68vh;
    }}
    .tree, .tree ul {{
      list-style: none;
      margin: 0;
      padding-left: 22px;
      position: relative;
    }}
    .tree {{
      padding-left: 0;
    }}
    .tree ul::before {{
      content: "";
      position: absolute;
      left: 8px;
      top: 0;
      bottom: 0;
      width: 1px;
      background: var(--line);
    }}
    .node {{
      position: relative;
      margin: 6px 0;
    }}
    .node::before {{
      content: "";
      position: absolute;
      left: -14px;
      top: 17px;
      width: 14px;
      height: 1px;
      background: var(--line);
    }}
    .tree > .node::before {{
      display: none;
    }}
    .row {{
      display: grid;
      grid-template-columns: 24px minmax(270px, 1fr) 100px 76px 92px;
      gap: 10px;
      align-items: center;
      min-height: 34px;
      padding: 5px 8px;
      border-radius: 6px;
    }}
    .row:hover {{
      background: #f0f3f6;
    }}
    .toggle {{
      width: 22px;
      height: 22px;
      padding: 0;
      border-radius: 4px;
      line-height: 20px;
      text-align: center;
      background: var(--soft);
      border-color: #badbd6;
      color: var(--accent);
    }}
    .toggle.empty {{
      visibility: hidden;
    }}
    .title {{
      min-width: 0;
    }}
    .name {{
      font-weight: 700;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .sub {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 52px;
      height: 24px;
      border-radius: 999px;
      background: #edf0f2;
      color: #344054;
      font-size: 12px;
      padding: 0 8px;
      white-space: nowrap;
    }}
    .level-1 > .row {{ border-left: 4px solid var(--accent); }}
    .level-2 > .row {{ border-left: 4px solid #3d8b7d; }}
    .level-3 > .row {{ border-left: 4px solid #8a9a5b; }}
    .level-4 > .row {{ border-left: 4px solid var(--accent-2); }}
    .hidden {{ display: none; }}
    mark {{
      background: var(--mark);
      padding: 0 2px;
    }}
    aside {{
      position: sticky;
      top: 102px;
      padding: 14px;
    }}
    aside h2 {{
      margin: 0 0 12px;
      font-size: 16px;
    }}
    .detail {{
      display: grid;
      grid-template-columns: 108px 1fr;
      gap: 8px 10px;
      line-height: 1.35;
      word-break: break-word;
    }}
    .detail .k {{
      color: var(--muted);
      font-size: 12px;
    }}
    .empty-state {{
      color: var(--muted);
      padding: 20px 4px;
    }}
    @media (max-width: 980px) {{
      .toolbar {{ grid-template-columns: 1fr 1fr; }}
      main {{ grid-template-columns: 1fr; }}
      aside {{ position: static; }}
      .summary {{ grid-template-columns: 1fr; }}
      .row {{ grid-template-columns: 24px minmax(210px, 1fr) 76px; }}
      .row .optional {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>BOM Tree Visualization</h1>
    <div class="toolbar">
      <input id="search" type="search" placeholder="부품명, P/No, Supplier 검색" />
      <select id="depth">
        <option value="{max_level}">전체 Level</option>
        {"".join(f'<option value="{i}">Level {i}까지</option>' for i in range(1, max_level + 1))}
      </select>
      <button id="expand" class="primary">전체 펼치기</button>
      <button id="collapse">Level 1만</button>
    </div>
  </header>
  <main>
    <section>
      <div class="summary">
        <div class="metric"><div class="label">총 BOM 노드</div><div class="value">{total_nodes:,}</div></div>
        <div class="metric"><div class="label">최상위 노드</div><div class="value">{root_count:,}</div></div>
        <div class="metric"><div class="label">최대 Level</div><div class="value">{max_level}</div></div>
      </div>
      <div class="tree-wrap">
        <ul id="tree" class="tree"></ul>
      </div>
    </section>
    <aside>
      <h2>선택한 부품</h2>
      <div id="details" class="empty-state">트리에서 노드를 선택하면 상세 정보가 표시됩니다.</div>
    </aside>
  </main>
  <script>
    const roots = {data_json};
    const flat = {flat_json};
    const flatById = new Map(flat.map(d => [d.id, d]));
    const collapsed = new Set();
    const treeEl = document.getElementById("tree");
    const detailsEl = document.getElementById("details");
    const searchEl = document.getElementById("search");
    const depthEl = document.getElementById("depth");

    function nodeText(node) {{
      return [node.name, node.newNo, node.baseNo, node.partType, node.supplier, node.classification, node.no].join(" ").toLowerCase();
    }}

    function hasMatch(node, query) {{
      if (!query) return true;
      if (nodeText(node).includes(query)) return true;
      return node.children.some(child => hasMatch(child, query));
    }}

    function highlight(text, query) {{
      text = String(text || "");
      if (!query) return escapeHtml(text);
      const idx = text.toLowerCase().indexOf(query);
      if (idx < 0) return escapeHtml(text);
      return escapeHtml(text.slice(0, idx)) + "<mark>" + escapeHtml(text.slice(idx, idx + query.length)) + "</mark>" + escapeHtml(text.slice(idx + query.length));
    }}

    function escapeHtml(text) {{
      return String(text).replace(/[&<>"']/g, m => ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }}[m]));
    }}

    function render() {{
      const query = searchEl.value.trim().toLowerCase();
      const maxDepth = Number(depthEl.value);
      treeEl.innerHTML = "";
      roots.forEach(node => {{
        const li = renderNode(node, query, maxDepth);
        if (li) treeEl.appendChild(li);
      }});
    }}

    function renderNode(node, query, maxDepth) {{
      if (node.level > maxDepth || !hasMatch(node, query)) return null;
      const li = document.createElement("li");
      li.className = `node level-${{Math.min(node.level, 4)}}`;
      li.dataset.id = node.id;

      const row = document.createElement("div");
      row.className = "row";

      const hasChildren = node.children.some(child => child.level <= maxDepth && hasMatch(child, query));
      const button = document.createElement("button");
      button.className = "toggle" + (hasChildren ? "" : " empty");
      button.textContent = collapsed.has(node.id) ? "+" : "-";
      button.title = collapsed.has(node.id) ? "펼치기" : "접기";
      button.addEventListener("click", event => {{
        event.stopPropagation();
        collapsed.has(node.id) ? collapsed.delete(node.id) : collapsed.add(node.id);
        render();
      }});
      row.appendChild(button);

      const title = document.createElement("div");
      title.className = "title";
      title.innerHTML = `<div class="name">${{highlight(node.name || "(부품명 없음)", query)}}</div>
        <div class="sub">No. ${{escapeHtml(node.no)}} · ${{escapeHtml(node.newNo || node.baseNo)}} · ${{escapeHtml(node.partType)}}</div>`;
      row.appendChild(title);

      const level = document.createElement("span");
      level.className = "pill";
      level.textContent = `Lv ${{node.level}}`;
      row.appendChild(level);

      const qty = document.createElement("span");
      qty.className = "pill optional";
      qty.textContent = node.qty === "" ? "Qty -" : `Qty ${{node.qty}}`;
      row.appendChild(qty);

      const cls = document.createElement("span");
      cls.className = "pill optional";
      cls.textContent = node.classification || "-";
      row.appendChild(cls);

      row.addEventListener("click", () => selectNode(node.id));
      li.appendChild(row);

      if (hasChildren && !collapsed.has(node.id)) {{
        const ul = document.createElement("ul");
        node.children.forEach(child => {{
          const childLi = renderNode(child, query, maxDepth);
          if (childLi) ul.appendChild(childLi);
        }});
        if (ul.children.length) li.appendChild(ul);
      }}
      return li;
    }}

    function selectNode(id) {{
      const node = flatById.get(id);
      if (!node) return;
      detailsEl.className = "detail";
      detailsEl.innerHTML = [
        ["No.", node.no],
        ["BOM Level", node.bomLevel],
        ["Part Type", node.partType],
        ["Base P/No", node.baseNo],
        ["New P/No", node.newNo],
        ["부품명", node.name],
        ["Q'ty", node.qty],
        ["Supplier", node.supplier],
        ["Classification", node.classification],
        ["Changing Point", node.changingPoint],
      ].map(([k, v]) => `<div class="k">${{escapeHtml(k)}}</div><div>${{escapeHtml(v || "-")}}</div>`).join("");
    }}

    document.getElementById("expand").addEventListener("click", () => {{
      collapsed.clear();
      render();
    }});
    document.getElementById("collapse").addEventListener("click", () => {{
      flat.forEach(node => collapsed.add(node.id));
      render();
    }});
    searchEl.addEventListener("input", render);
    depthEl.addEventListener("change", render);

    flat.forEach(node => {{
      if (node.level >= 2) collapsed.add(node.id);
    }});
    render();
  </script>
</body>
</html>"""


def main():
    df = pd.read_excel(INPUT, sheet_name="Master", header=7)
    rows = df[df["BOM\nLevel"].notna()].copy()
    tree = build_tree(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(render_html(tree), encoding="utf-8")

    levels = {}
    for node in tree["flat"]:
        levels[node["level"]] = levels.get(node["level"], 0) + 1
    print(json.dumps({
        "output": str(OUTPUT_HTML),
        "nodes": len(tree["flat"]),
        "roots": len(tree["roots"]),
        "max_level": max(levels) if levels else 0,
        "level_counts": levels,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
