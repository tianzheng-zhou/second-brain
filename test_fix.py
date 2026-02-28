"""测试 _postprocess_chunks 修复"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from personal_brain.core.indexer import _postprocess_chunks


def test_postprocess_chunks():
    """测试小块合并逻辑"""

    chunk_size = 1000
    chunk_overlap = 0  # Not used

    print("=" * 60)
    print("测试 _postprocess_chunks 函数")
    print("=" * 60)

    # 测试1: 多个连续小块
    print("\n【测试1】多个连续小块应该被合并")
    chunks1 = [
        "A" * 100,  # 太小 (< 400)
        "B" * 100,  # 太小
        "C" * 100,  # 太小
        "D" * 800,  # 正常大小
    ]
    result1 = _postprocess_chunks(chunks1, chunk_size, chunk_overlap)
    print(f"  输入: {len(chunks1)} 个块")
    print(f"  块大小: {[len(c) for c in chunks1]}")
    print(f"  输出: {len(result1)} 个块")
    print(f"  块大小: {[len(c) for c in result1]}")
    assert len(result1) == 1, f"应该合并为1个块，但得到 {len(result1)} 个"
    print("  [OK] 通过")

    # 测试2: 末尾小块
    print("\n【测试2】末尾小块应该与前一块合并")
    chunks2 = [
        "A" * 800,  # 正常
        "B" * 100,  # 太小
    ]
    result2 = _postprocess_chunks(chunks2, chunk_size, chunk_overlap)
    print(f"  输入: {len(chunks2)} 个块")
    print(f"  块大小: {[len(c) for c in chunks2]}")
    print(f"  输出: {len(result2)} 个块")
    print(f"  块大小: {[len(c) for c in result2]}")
    assert len(result2) == 1, f"应该合并为1个块，但得到 {len(result2)} 个"
    print("  [OK] 通过")

    # 测试3: 开头小块
    print("\n【测试3】开头小块应该与后一块合并")
    chunks3 = [
        "A" * 100,  # 太小
        "B" * 800,  # 正常
    ]
    result3 = _postprocess_chunks(chunks3, chunk_size, chunk_overlap)
    print(f"  输入: {len(chunks3)} 个块")
    print(f"  块大小: {[len(c) for c in chunks3]}")
    print(f"  输出: {len(result3)} 个块")
    print(f"  块大小: {[len(c) for c in result3]}")
    assert len(result3) == 1, f"应该合并为1个块，但得到 {len(result3)} 个"
    print("  [OK] 通过")

    # 测试4: 全是小块
    print("\n【测试4】全是小块应该全部合并")
    chunks4 = [
        "A" * 100,
        "B" * 100,
        "C" * 100,
    ]
    result4 = _postprocess_chunks(chunks4, chunk_size, chunk_overlap)
    print(f"  输入: {len(chunks4)} 个块")
    print(f"  块大小: {[len(c) for c in chunks4]}")
    print(f"  输出: {len(result4)} 个块")
    print(f"  块大小: {[len(c) for c in result4]}")
    assert len(result4) == 1, f"应该合并为1个块，但得到 {len(result4)} 个"
    print("  [OK] 通过")

    # 测试5: 正常块不被错误合并
    print("\n【测试5】正常大小的块应该保持独立")
    chunks5 = [
        "A" * 800,
        "B" * 800,
    ]
    result5 = _postprocess_chunks(chunks5, chunk_size, chunk_overlap)
    print(f"  输入: {len(chunks5)} 个块")
    print(f"  块大小: {[len(c) for c in chunks5]}")
    print(f"  输出: {len(result5)} 个块")
    print(f"  块大小: {[len(c) for c in result5]}")
    assert len(result5) == 2, f"应该保持2个块，但得到 {len(result5)} 个"
    print("  [OK] 通过")

    # 测试6: 混合场景
    print("\n【测试6】复杂混合场景")
    chunks6 = [
        "A" * 100,  # 小
        "B" * 800,  # 正常
        "C" * 100,  # 小
        "D" * 100,  # 小
        "E" * 800,  # 正常
        "F" * 100,  # 小
    ]
    result6 = _postprocess_chunks(chunks6, chunk_size, chunk_overlap)
    print(f"  输入: {len(chunks6)} 个块")
    print(f"  块大小: {[len(c) for c in chunks6]}")
    print(f"  输出: {len(result6)} 个块")
    print(f"  块大小: {[len(c) for c in result6]}")
    # 期望：A+B, C+D+E, F -> 3个块（但 F 会合并到 E，所以是 2 个？）
    # 实际：A合并到B, C+D合并到E, F合并到E -> 2个块
    assert len(result6) == 2, f"应该合并为2个块，但得到 {len(result6)} 个"
    print("  [OK] 通过")

    print("\n" + "=" * 60)
    print("所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    test_postprocess_chunks()
