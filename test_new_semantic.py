"""测试新的语义单元分割"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 读取新函数
exec(open('new_semantic_units.py').read())

# 实际文档内容
text = """# 1TFT_1PT 制备流程

Bowen Zhu lab

2023/07/12

西湖大学

# Step 1 Substrate cleaning (~2 h)

1) 去离子水（DIW）超声清洗 10 mins

2) SC1（ $( N H _ { 4 } O H ; H _ { 2 } O _ { 2 } : H _ { 2 } O$ 体积比1 (70ml)：2 (140ml)：7 (490ml)）setting value: $60 \\%$ (实际 $70 \\%$ )超声清洗 10 mins（此步骤主要用于去除有机物污染）

3) 去离子水（DIW）超声清洗10 mins

4) SC2 $( \\mathsf { H C l } , \\mathsf { H } _ { 2 } \\mathsf { O } _ { 2 } , \\mathsf { H } _ { 2 } \\mathsf { O }$ 体积比 1 (90ml)：1 (90ml)：6 (540ml)) $70 \\%$ 超声清洗10 mins（此步骤主要用于去除金属污染）

5) 去离子水（DIW）超声清洗10 mins （可以把镊子一起用水清洗）

6) 清洗完1 $10 \\%$ 烘烤1-2 min 去除水分

# 注意事项：

1.提前将117房间超声清洗机加热设置到60摄氏度（超声清洗机实际温度比显示温度高，因此显示温度达到$5 5 \\%$ 即可开始清洗）

2.若基底没有金属污染，只需要进行前三步清洗

3.可以直接用新拆硅片

# Step 2 lithograph gate electrode (~2 h)

AR300-80

1) 旋涂AR300-80 增附剂 （recipe:AR300-80 rhh）

2) $90 \\%$ 烘烤90 s

3) 旋涂5350光刻胶（recipe:rhh-5350）

4) $105 C$ 烘烤4mins

5) 对准曝光（recipe name:rhh-5350）

6) 显影液AR 300-26:H2O (体积比1：6) 显影时间 45s (也可以加热显影)

7) 用去离子水冲洗并用氮气枪吹干

8) 可以 $105 C$ 烘烤1 min去除水分

注意事项：1.旋涂前烘烤把基底水分蒸发

2.旋涂光刻胶前，将光刻胶冷却至室温。（光刻胶会吸水）

3.AR3300-80为了5350更好的显影

4.旋涂前不要打plasma

<table><tr><td>Step</td><td>Time (s)</td><td>Speed (rpm)</td><td>Acc</td></tr><tr><td>1</td><td>5</td><td>400</td><td>400</td></tr><tr><td>2</td><td>40-60</td><td>4000</td><td>1500</td></tr></table>

5350

<table><tr><td>Step</td><td>Time (s)</td><td>Speed (rpm)</td><td>Acc</td></tr><tr><td>1</td><td>5</td><td>400</td><td>400</td></tr><tr><td>2</td><td>40</td><td>4000</td><td>1500</td></tr></table>

# Step 3 E-beam evaporate bottom gate (~3h)

1.大蒸镀挂架（抽真空时间大概一个小时）

2.选择Ni/Au (8/50 nm)

3.取出样品

注意事项：1.使用前确认原料 (一般1号是Ni 3号是Au)

2.Ni坩埚下面要放置一个圆圈散热 （Au是没有的）

# Step 4 Lift off

1.超声清洗机加热设定到 $30 \\%$ (不要超声, 不要用热台)

2.丙酮浸泡 $2 \\\\sim 5$ min

3.用丙酮-异丙醇-(水)清洗后吹干 (可以不用水 IPA后直接吹干)

4.吹干后 $105 \\\\textcircled { ‰}$ 去除水分

注意事项：1.丙酮浸泡时间可以看情况延长或缩短

2.丙酮要把金属冲干净，吹干后很难再去掉了"""


def analyze():
    """分析语义单元分割"""
    print("=" * 80)
    print("测试新的语义单元分割（按章节分组）")
    print("=" * 80)

    units, boundaries = _split_into_semantic_units(text)

    print(f"\n总共分割为 {len(units)} 个语义单元：\n")

    for i, (unit, (start, end, _)) in enumerate(zip(units, boundaries)):
        lines = unit.split('\n')
        first_line = lines[0][:60] if lines[0] else "(空)"

        # 检测单元类型
        marker = ""
        if unit.strip().startswith('```'):
            marker = "[代码块]"
        elif unit.strip().startswith('|'):
            marker = "[MD表格]"
        elif unit.strip().lower().startswith('<table'):
            marker = "[HTML表格]"
        elif unit.strip().startswith('#'):
            marker = "[章节]"

        print(f"[{i:2d}] {marker:12s} ({len(unit):5d} 字符) 首行: {first_line}")

    print("\n" + "=" * 80)
    print("详细内容预览（前3个单元）：")
    print("=" * 80)

    for i, unit in enumerate(units[:3]):
        print(f"\n--- 单元 {i} ({len(unit)} 字符) ---")
        preview = unit[:300].replace('\n', ' ')
        if len(unit) > 300:
            preview += "..."
        print(preview)


if __name__ == "__main__":
    analyze()
