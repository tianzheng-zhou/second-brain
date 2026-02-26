#!/usr/bin/env python
"""
测试语义分割器 vs 传统字符分割器的效果对比

新方案核心改进：
- 不再让 LLM 数字符（它不擅长）
- 而是让 LLM 识别哪些段落/单元是好的分割边界
- 由 Python 代码自己计算实际的字符位置
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from personal_brain.core.indexer import (
    recursive_character_text_splitter,
    semantic_text_splitter
)
from personal_brain.config import SEMANTIC_SPLIT_MODEL

# 示例文本：包含多个主题段落的长文本（模拟 PDF 提取的内容）
SAMPLE_TEXT = """
人工智能 (Artificial Intelligence, AI) 是计算机科学的一个分支，它试图理解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。该领域的研究包括机器人、语言识别、图像识别、自然语言处理和专家系统等。人工智能从诞生以来，理论和技术日益成熟，应用领域也不断扩大。

机器学习是人工智能的核心领域之一，它研究如何让计算机通过经验自动改进。深度学习是机器学习的一个子集，它使用多层神经网络来学习数据的层次化表示。卷积神经网络 (CNN) 在图像处理领域取得了巨大成功，如图 1 所示。

![CNN 架构图](images/cnn_architecture.png)

图 1: 卷积神经网络的基本架构，包括卷积层、池化层和全连接层。

自然语言处理 (NLP) 是人工智能和语言学的重要交叉领域，它研究如何让计算机理解、解释和生成人类语言。近年来的大语言模型如 GPT 系列、Claude 等，展示了令人惊叹的语言理解和生成能力，在文本摘要、翻译、问答等任务上达到了前所未有的水平。

在医疗领域，AI 技术正在改变传统的诊疗模式。通过分析医学影像，AI 可以辅助医生进行疾病诊断；通过分析电子病历，AI 可以预测患者的健康风险；在药物研发方面，AI 可以大幅缩短新药研发的周期和降低成本。AlphaFold 的成功更是展示了 AI 在蛋白质结构预测这一基础科学问题上的巨大潜力。

![AI 医疗应用场景](images/ai_healthcare.png)

图 2: AI 在医疗领域的三大应用场景：医学影像、电子病历分析和药物研发。

教育领域也是 AI 应用的重要场景。个性化学习系统可以根据学生的学习进度和理解能力，动态调整教学内容和难度；智能辅导系统可以 24 小时为学生提供答疑服务；自动批改系统可以减轻教师的工作负担，让他们有更多时间关注学生的个性化需求。

金融行业中，AI 被广泛应用于风险控制、欺诈检测、智能投顾等领域。通过分析用户的交易行为和信用历史，AI 模型可以准确评估信贷风险；通过实时监控交易模式，AI 可以及时发现异常交易行为，防止金融欺诈。

制造业正在经历数字化转型，工业 4.0 的概念逐渐落地。智能机器人可以在生产线上完成精密装配任务；预测性维护系统可以通过传感器数据预测设备故障，减少停机时间；供应链优化系统可以实时调整库存和物流策略，提高运营效率。

自动驾驶技术是 AI 在交通领域最具代表性的应用。通过融合摄像头、激光雷达、毫米波雷达等多种传感器的数据，自动驾驶汽车可以实时感知周围环境，做出驾驶决策。虽然完全自动驾驶的商业化还面临诸多挑战，但辅助驾驶系统已经在量产车上得到广泛应用。

伦理和安全问题伴随着 AI 技术的快速发展而日益突出。算法偏见可能导致不公平的决策结果；深度伪造技术可能被用于制造虚假信息；自主武器系统可能带来新的安全威胁；大规模自动化可能导致就业结构的变化。如何在推动 AI 发展的同时，确保其安全、可控、向善，是全球共同面临的课题。

展望未来，通用人工智能 (AGI) 仍然是该领域的长期目标。AGI 系统将具备类似人类的广泛认知能力，可以跨领域学习和适应。虽然目前的技术距离 AGI 还有很长的路要走，但各国科研机构和企业正在这一方向上持续投入，探索新的算法和架构。
"""


def compare_splits(text, image_root=None):
    """比较两种分割方式的效果"""
    chunk_size = 800
    chunk_overlap = 150

    print("=" * 80)
    print("分割效果对比测试")
    print("=" * 80)
    print(f"使用模型：{SEMANTIC_SPLIT_MODEL}")
    print(f"文本长度：{len(text)} 字符")
    print(f"目标块大小：{chunk_size} 字符")
    print(f"重叠：{chunk_overlap} 字符")
    if image_root:
        print(f"图片目录：{image_root}")
    print("=" * 80)

    # 传统字符分割
    print("\n【传统字符分割结果】\n")
    char_chunks = recursive_character_text_splitter(text, chunk_size, chunk_overlap)
    for i, chunk in enumerate(char_chunks):
        print(f"--- Chunk {i+1} ({len(chunk)} 字符) ---")
        preview = chunk[:150] + "..." if len(chunk) > 150 else chunk
        # 标记是否包含图片
        img_marker = "[含图片] " if "!" in chunk else ""
        print(f"{img_marker}{preview}")
        print()

    print(f"传统分割：共 {len(char_chunks)} 个块")
    print()

    # 语义分割
    print("\n【语义分割结果】\n")
    semantic_chunks = semantic_text_splitter(text, image_root=image_root, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    for i, chunk in enumerate(semantic_chunks):
        print(f"--- Chunk {i+1} ({len(chunk)} 字符) ---")
        preview = chunk[:150] + "..." if len(chunk) > 150 else chunk
        # 标记是否包含图片
        img_marker = "[含图片] " if "!" in chunk else ""
        print(f"{img_marker}{preview}")
        print()

    print(f"语义分割：共 {len(semantic_chunks)} 个块")
    print()

    # 对比统计
    print("=" * 80)
    print("【统计对比】")
    print("=" * 80)
    print(f"{'指标':<15} | {'传统分割':<15} | {'语义分割':<15}")
    print("-" * 50)
    print(f"{'块数量':<15} | {len(char_chunks):<15} | {len(semantic_chunks):<15}")
    print(f"{'平均块大小':<15} | {sum(len(c) for c in char_chunks)//len(char_chunks):<15} | {sum(len(c) for c in semantic_chunks)//len(semantic_chunks):<15}")
    print(f"{'最小块':<15} | {min(len(c) for c in char_chunks):<15} | {min(len(c) for c in semantic_chunks):<15}")
    print(f"{'最大块':<15} | {max(len(c) for c in char_chunks):<15} | {max(len(c) for c in semantic_chunks):<15}")

    # 检查图片是否保持完整
    char_img_count = sum(1 for c in char_chunks if "!" in c)
    semantic_img_count = sum(1 for c in semantic_chunks if "!" in c)
    total_imgs = text.count("![")
    print(f"\n图片完整性检查:")
    print(f"  原文图片数：{total_imgs}")
    print(f"  传统分割包含图片的块：{char_img_count}")
    print(f"  语义分割包含图片的块：{semantic_img_count}")


if __name__ == "__main__":
    # 测试纯文本分割
    compare_splits(SAMPLE_TEXT)

    # 测试带图片的分割（如果存在图片目录）
    from pathlib import Path
    test_image_root = Path(__file__).parent / "test_images"
    if test_image_root.exists():
        print("\n\n" + "=" * 80)
        print("测试带图片的文档分割")
        print("=" * 80 + "\n")
        compare_splits(SAMPLE_TEXT, image_root=test_image_root)