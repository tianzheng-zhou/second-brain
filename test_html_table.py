"""测试 HTML 表格语义分割"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from personal_brain.core.indexer import _split_into_semantic_units


def test_html_table_splitting():
    """测试 HTML 表格作为独立语义单元"""

    text = """# 文档标题

这是一些介绍文字。

<table>
<tr><th>步骤</th><th>描述</th></tr>
<tr><td>1</td><td>第一步操作</td></tr>
<tr><td>2</td><td>第二步操作</td></tr>
</table>

这是表格后的文字。

| Markdown | 表格 |
|----------|------|
| 单元格1  | 单元格2 |

结束文字。"""

    print("=" * 60)
    print("测试语义单元分割")
    print("=" * 60)

    units, boundaries = _split_into_semantic_units(text)

    print(f"\n总共分割为 {len(units)} 个语义单元：\n")

    for i, (unit, (start, end, _)) in enumerate(zip(units, boundaries)):
        preview = unit[:100].replace('\n', ' ')
        if len(unit) > 100:
            preview += "..."

        # 检测单元类型
        unit_type = "文本"
        if unit.strip().startswith('```'):
            unit_type = "代码块"
        elif unit.strip().startswith('|'):
            unit_type = "Markdown表格"
        elif unit.strip().lower().startswith('<table'):
            unit_type = "HTML表格"
        elif unit.strip().startswith('<'):
            unit_type = "HTML"

        print(f"[{i}] {unit_type} ({len(unit)} 字符)")
        print(f"    位置: {start}-{end}")
        print(f"    预览: {preview}")
        print()

    # 验证 HTML 表格是否被识别为独立单元
    html_table_units = [u for u in units if u.strip().lower().startswith('<table')]
    markdown_table_units = [u for u in units if u.strip().startswith('|')]

    print("=" * 60)
    print("验证结果")
    print("=" * 60)

    if html_table_units:
        print(f"[OK] 找到 {len(html_table_units)} 个 HTML 表格单元")
        # 检查 HTML 表格是否完整
        for u in html_table_units:
            if '</table>' in u.lower():
                print("[OK] HTML 表格包含完整的结束标签")
            else:
                print("[WARNING] HTML 表格可能不完整")
    else:
        print("[WARNING] 没有找到 HTML 表格单元")

    if markdown_table_units:
        print(f"[OK] 找到 {len(markdown_table_units)} 个 Markdown 表格单元")
    else:
        print("[WARNING] 没有找到 Markdown 表格单元")

    # 检查所有单元是否都在边界内
    print("\n边界检查：")
    all_valid = True
    for i, (start, end, _) in enumerate(boundaries):
        if end <= start:
            print(f"[ERROR] 单元 {i} 边界无效: {start}-{end}")
            all_valid = False
        if i > 0:
            prev_end = boundaries[i-1][1]
            if start != prev_end:
                print(f"[WARNING] 单元 {i-1} 和 {i} 之间可能有间隙或重叠")
                print(f"        单元 {i-1} 结束于 {prev_end}, 单元 {i} 开始于 {start}")

    if all_valid:
        print("[OK] 所有边界有效")

    return units, boundaries


if __name__ == "__main__":
    test_html_table_splitting()
