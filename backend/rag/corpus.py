"""
法律语料加载与法条解析。

设计目标：
1. 不再把整部法律按固定字数粗暴切断，而是以「条」为最小检索单元；
2. 每个块都带上可追溯的元数据：法规名、条号、章节、版本、来源；
3. 对没有条号结构的模板文件（如保密协议模板）保留普通切块兜底。

语料来源见 legal_data/manifest.json 与项目根目录的 SOURCES.md。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LEGAL_DATA_DIR = Path(__file__).parent / "legal_data"
MANIFEST_PATH = LEGAL_DATA_DIR / "manifest.json"

# 行首的「第X条」。支持「第X条之一」以及阿拉伯数字写法。
ARTICLE_RE = re.compile(
    r"^(第[一二三四五六七八九十百千万零〇两0-9]+条(?:之[一二三四五六七八九十]+)?)\s*(.*)$"
)
# 章节标题：# / ## / ### 开头，例如「## 第一章 总 则」「### 第一分编 通则」
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
# 只把「第X编/章/节/分编」当作结构标题，避免把「# 合同编」之外的内容误判
SECTION_RE = re.compile(r"^第[一二三四五六七八九十百千万零〇两0-9]+[编章节分].*$")

# 零宽字符与 BOM 会污染检索和引用匹配，读入时统一清掉
_INVISIBLE = dict.fromkeys(map(ord, "﻿​‌‍"), None)

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def clean_text(text: str) -> str:
    """去掉 BOM/零宽字符，并统一换行符。"""
    text = text.translate(_INVISIBLE)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def chinese_to_int(value: str) -> int:
    """把「四百六十三」「一千二百六十」等中文数字转成整数。

    只处理法条编号会用到的范围（0–99999）。非法输入返回 0。
    """
    value = value.strip()
    if not value:
        return 0
    if value.isdigit():
        return int(value)

    total = 0
    section = 0
    number = 0
    for char in value:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                if number == 0:
                    number = 1
                section += number * unit
            number = 0
        else:
            return 0
    return total + section + number


def article_number(article: str) -> int:
    """从「第四百六十三条」中取出 463；「第十条之一」取 10。"""
    match = re.search(r"第([一二三四五六七八九十百千万零〇两0-9]+)条", article)
    if not match:
        return 0
    return chinese_to_int(match.group(1))


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, dict[str, Any]]:
    """读取语料清单；不存在时返回空字典，保证旧目录也能加载。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"⚠️ 语料清单读取失败，忽略元数据: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _document_meta(filepath: Path, manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
    meta = manifest.get(filepath.name, {})
    return {
        "law": meta.get("law", ""),
        "short_name": meta.get("short", ""),
        "book": meta.get("book", ""),
        "version_date": meta.get("version_date", ""),
        "effective_date": meta.get("effective_date", ""),
        "source_url": meta.get("source_url", ""),
        "source_repo": meta.get("source_repo", ""),
        "source_path": meta.get("source_path", ""),
        "is_template": bool(meta.get("is_template", False)),
    }


def load_legal_documents(data_dir: Path = LEGAL_DATA_DIR) -> list[dict]:
    """加载目录下所有 .md 法律文件。

    返回字段保持与旧版兼容（title/content/source），并额外附带
    law/short_name/book/version_date/source_url 等检索与引用所需信息。
    """
    documents: list[dict] = []
    if not data_dir.exists():
        print(f"⚠️ 法律数据目录不存在: {data_dir}")
        return documents

    manifest = load_manifest(data_dir / "manifest.json")

    for filepath in sorted(data_dir.glob("*.md")):
        try:
            content = clean_text(filepath.read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"⚠️ 读取失败: {filepath.name}: {exc}")
            continue
        if not content.strip():
            continue

        titles = []
        for line in content.splitlines():
            heading = HEADING_RE.match(line)
            if heading and len(heading.group(1)) == 1:
                titles.append(heading.group(2).strip())
        fallback_title = titles[0] if titles else filepath.stem

        meta = _document_meta(filepath, manifest)
        law = meta["law"] or fallback_title
        short_name = meta["short_name"] or law
        book = meta["book"]
        if not book and len(titles) > 1 and "民法典" in law:
            # 民法典分编文件：第二个一级标题是「合同编」「婚姻家庭编」等
            book = titles[1]

        documents.append({
            "title": law,
            "content": content,
            "source": filepath.name,
            "law": law,
            "short_name": short_name,
            "book": book,
            "version_date": meta["version_date"],
            "effective_date": meta["effective_date"],
            "source_url": meta["source_url"],
            "source_repo": meta["source_repo"],
            "source_path": meta["source_path"],
            "is_template": meta["is_template"],
        })

    return documents


def extract_articles(content: str) -> list[dict]:
    """按「第X条」切分文本，返回 [{article, article_num, text, chapter}]。

    - 条号必须出现在行首；
    - 保留章节路径，便于展示「《民法典》合同编 第九章 买卖合同 第五百九十五条」；
    - 文本中不含 `<!-- INFO END -->` 之类的构建标记。
    """
    content = clean_text(content)
    articles: list[dict] = []
    headings: dict[int, str] = {}
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        text = "\n".join(current.pop("lines")).strip()
        if text:
            chapter = " / ".join(
                headings[level] for level in sorted(headings) if headings[level]
            )
            articles.append({
                "article": current["article"],
                "article_num": current["article_num"],
                "text": text,
                "chapter": chapter,
            })
        current = None

    for line in content.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            # 先结算上一条：章节标题出现在两条之间时，上一条仍属于旧章节。
            # 若先更新 headings 再 flush，每条章节的「最后一条」都会被记到下一章。
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if level > 1 and SECTION_RE.match(title):
                headings[level] = title
                # 新的一级章节出现时，清掉更深的旧标题
                for deeper in [k for k in headings if k > level]:
                    del headings[deeper]
            continue

        match = ARTICLE_RE.match(line)
        if match:
            flush()
            article = match.group(1)
            current = {
                "article": article,
                "article_num": article_number(article),
                "lines": [line],
            }
            continue

        if current is not None:
            current["lines"].append(line)

    flush()
    return articles


def retrieval_text(law: str, article: str, body: str) -> str:
    """给条文块加一行来源前缀，提升 embedding 与展示的可读性。"""
    label = f"《{law}》{article}" if law and article else ""
    if label and label not in body[:80]:
        return f"{label}\n{body}"
    return body
