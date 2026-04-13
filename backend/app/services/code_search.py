"""
代码库搜索服务

在 AI 分析 issue 之前，先从本地代码仓库中找到与 issue 相关的真实文件和代码片段，
作为上下文传给 AI，使其只引用真实存在的文件路径，彻底杜绝幻觉路径。
"""
import re
from pathlib import Path

# 扫描的文件扩展名
_SEARCH_EXTS = {'.cpp', '.c', '.h', '.hpp', '.py', '.yaml', '.yml', '.json', '.txt', '.md'}

# 跳过的目录
_SKIP_DIRS = {'.git', 'node_modules', 'build', 'dist', '.venv', '__pycache__',
              '.cache', 'third_party', 'thirdparty', 'vendor', '.mypy_cache'}


def extract_keywords(title: str, description: str) -> list[str]:
    """
    从 issue 标题和描述中提取搜索关键词。
    - 英文：提取完整标识符（变量名、函数名等）
    - 中文：按标点切词后取有意义的短语，不做硬截断
    """
    text = f"{title} {description}"
    keywords: list[str] = []

    # 英文标识符（含下划线、驼峰，至少3字符）
    for m in re.finditer(r'[A-Za-z][A-Za-z0-9_]{2,}', text):
        keywords.append(m.group())

    # 中文：先按标点和空格切分成短语，再提取2~6字的连续汉字词组
    # 按非汉字符号切分
    chinese_segments = re.split(r'[^\u4e00-\u9fff]+', text)
    for seg in chinese_segments:
        seg = seg.strip()
        if len(seg) < 2:
            continue
        # 对长段落提取所有2~4字子串作为候选词（滑动窗口）
        for size in (4, 3, 2):
            for i in range(len(seg) - size + 1):
                chunk = seg[i:i + size]
                if chunk not in keywords:
                    keywords.append(chunk)
            if len(keywords) > 30:
                break

    # 去重（大小写不敏感），过滤过于通用的词
    _stop = {'the', 'and', 'for', 'with', 'this', 'that', 'from', 'into',
             '问题', '描述', '阶段', '情况', '进行', '相关', '导致', '原因',
             '不当', '未正', '数据', '配置', '无法'}
    seen: set[str] = set()
    result: list[str] = []
    for k in keywords:
        kl = k.lower()
        if kl not in seen and kl not in _stop:
            seen.add(kl)
            result.append(k)

    return result[:30]  # 最多30个关键词


def search_codebase(codebase_path: str, keywords: list[str],
                    max_files: int = 6, context_lines: int = 4) -> list[dict]:
    """
    在代码库中搜索含关键词的文件，返回匹配文件及相关代码片段。

    Returns:
        [
          {
            "file": "machine_arm/arm.cpp",   # 相对路径
            "matches": [
              {"line": 387, "snippet": "...上下文代码..."}
            ]
          },
          ...
        ]
    """
    if not codebase_path:
        return []
    root = Path(codebase_path)
    if not root.is_dir():
        return []

    results: list[dict] = []

    for file_path in sorted(root.rglob('*')):
        if len(results) >= max_files:
            break
        if not file_path.is_file():
            continue
        if file_path.suffix not in _SEARCH_EXTS:
            continue
        # 跳过忽略目录
        if any(part in _SKIP_DIRS for part in file_path.parts):
            continue

        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        lines = content.splitlines()
        content_lower = content.lower()

        # 文件里有没有任何关键词
        hit_keywords = [k for k in keywords if k.lower() in content_lower]
        if not hit_keywords:
            continue

        # 找到命中行，加上下文
        seen_ranges: set[int] = set()
        matches: list[dict] = []
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if not any(k.lower() in line_lower for k in keywords):
                continue
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            if i in seen_ranges:
                continue
            seen_ranges.update(range(start, end))
            snippet_lines = [f"{start + j + 1:4d}: {lines[start + j]}"
                             for j in range(end - start)]
            matches.append({"line": i + 1, "snippet": "\n".join(snippet_lines)})
            if len(matches) >= 4:  # 每个文件最多4处匹配
                break

        if matches:
            rel = str(file_path.relative_to(root))
            results.append({"file": rel, "matches": matches})

    return results


def format_for_prompt(search_results: list[dict]) -> str:
    """
    将搜索结果格式化为 AI prompt 可用的字符串。
    文件路径用 // File: 标注，方便 AI 提取真实路径。
    """
    if not search_results:
        return ""
    parts: list[str] = []
    for r in search_results:
        file_header = f"// File: {r['file']}"
        snippets = "\n...\n".join(m["snippet"] for m in r["matches"])
        parts.append(f"{file_header}\n{snippets}")
    return "\n\n".join(parts)
