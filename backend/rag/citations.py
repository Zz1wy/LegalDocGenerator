"""
法条引用抽取与「引用落地」校验。

这是法律场景区别于通用 RAG 的关键一步：
模型很容易写出「《劳动合同法》第八十二条」这种看起来很像真的条号，
但条号可能不存在、可能属于另一部法律、也可能与检索到的内容不一致。
这里做的是**可机械验证的那部分**——条号是否真实存在于本地语料；
语义是否恰当仍需人工复核，不能宣称「保证准确」。
"""

from __future__ import annotations

import re

from .corpus import article_number

# 《法名》第X条；允许中间有空格/全角空格
_CITATION_RE = re.compile(
    r"《\s*([^》\n]{2,40}?)\s*》\s*(第[一二三四五六七八九十百千万零〇两0-9]+条(?:之[一二三四五六七八九十]+)?)"
)

# 同一法名后的枚举写法：「《民事诉讼法》第一百二十二条、第五十四条、第二百八十五条」
# 只有紧跟在顿号/和/及/以及之后的「第X条」才归入同一部法律，避免把普通正文当成引用。
_ARTICLE_LIST_RE = re.compile(
    r"(?:\s*(?:以及|[、,，和及])\s*)"
    r"(第[一二三四五六七八九十百千万零〇两0-9]+条(?:之[一二三四五六七八九十]+)?)"
)

# 常见简称/别名 → 语料里的规范简称
_LAW_ALIASES = {
    "民法典": "民法典",
    "中华人民共和国民法典": "民法典",
    "劳动合同法": "劳动合同法",
    "中华人民共和国劳动合同法": "劳动合同法",
    "劳动法": "劳动法",
    "中华人民共和国劳动法": "劳动法",
    "公司法": "公司法",
    "中华人民共和国公司法": "公司法",
    "民事诉讼法": "民事诉讼法",
    "中华人民共和国民事诉讼法": "民事诉讼法",
    "民诉法": "民事诉讼法",
    "反不正当竞争法": "反不正当竞争法",
    "中华人民共和国反不正当竞争法": "反不正当竞争法",
}


def normalize_law_name(name: str) -> str:
    """把「中华人民共和国劳动合同法」统一成「劳动合同法」。"""
    name = (name or "").strip().strip("《》").replace(" ", "").replace("　", "")
    name = name.removeprefix("中华人民共和国")
    return _LAW_ALIASES.get(name, name)


def normalize_article(article: str) -> tuple[int, str]:
    """返回 (条号, 后缀)。「第十条之一」→ (10, '之一')。"""
    article = (article or "").strip().replace(" ", "").replace("　", "")
    number = article_number(article)
    suffix_match = re.search(r"条(之[一二三四五六七八九十]+)$", article)
    suffix = suffix_match.group(1) if suffix_match else ""
    return number, suffix


def citation_key(law: str, article: str) -> tuple[str, int, str]:
    number, suffix = normalize_article(article)
    return normalize_law_name(law), number, suffix


def extract_citations(text: str) -> list[dict]:
    """从生成文本中抽取所有显式法条引用，去重并保持出现顺序。

    除了完整的《法名》第X条，也支持同一法名后的枚举：
    「《民事诉讼法》第一百二十二条、第五十四条、第二百八十五条」
    会被抽成三条，均归入《民事诉讼法》。法律文书里这种写法很常见，
    只抓第一条会让「引用可追溯率」被低估。
    """
    text = text or ""
    seen: set[tuple[str, int, str]] = set()
    found: list[dict] = []

    def add(law: str, article: str, raw: str) -> None:
        key = citation_key(law, article)
        if key in seen:
            return
        seen.add(key)
        number, suffix = normalize_article(article)
        found.append({
            "raw": raw,
            "law": law,
            "article": article,
            "article_num": number,
            "suffix": suffix,
        })

    for match in _CITATION_RE.finditer(text):
        law = normalize_law_name(match.group(1))
        add(law, match.group(2), match.group(0))

        # 继续吃掉紧跟其后的「、第Y条」，直到遇到另一部法律或普通正文
        position = match.end()
        while True:
            extra = _ARTICLE_LIST_RE.match(text, position)
            if extra is None:
                break
            add(law, extra.group(1), extra.group(0).strip())
            position = extra.end()

    return found


def build_corpus_lookup(chunks: list[dict]) -> dict[tuple[str, int, str], dict]:
    """构建 (法规简称, 条号, 后缀) → 条文块 的索引。"""
    lookup: dict[tuple[str, int, str], dict] = {}
    for chunk in chunks:
        law = chunk.get("short_name") or chunk.get("law") or ""
        article = chunk.get("article") or ""
        if not law or not article:
            continue
        lookup.setdefault(citation_key(law, article), chunk)
    return lookup


def verify_citations(text: str, chunks: list[dict]) -> dict:
    """校验生成文本中的引用是否真实存在于本地语料。

    返回:
        {
          "valid":   [{law, article, source_url, ...}],
          "invalid": [{raw, law, article, reason}],
          "checked": 引用总数
        }

    注意：valid 只代表「条号存在」，不代表「引用内容与本案事实匹配」。
    """
    lookup = build_corpus_lookup(chunks)
    citations = extract_citations(text)
    valid: list[dict] = []
    invalid: list[dict] = []

    for citation in citations:
        key = citation_key(citation["law"], citation["article"])
        chunk = lookup.get(key)
        if chunk is None:
            invalid.append({
                "raw": citation["raw"],
                "law": citation["law"],
                "article": citation["article"],
                "reason": "本地语料中不存在该法规/条号",
            })
        else:
            valid.append({
                "raw": citation["raw"],
                "law": citation["law"],
                "article": citation["article"],
                "title": chunk.get("book") or chunk.get("law", ""),
                "source_url": chunk.get("source_url", ""),
                "text": chunk.get("text", "")[:200],
            })

    return {
        "valid": valid,
        "invalid": invalid,
        "checked": len(citations),
    }


def format_source_block(citations: list[dict]) -> str:
    """把检索结果格式化成 Prompt 中带编号的「可引用条文」区块。"""
    if not citations:
        return ""
    lines = ["【可引用法律条文（仅可引用以下条文，不得编造）】"]
    for index, item in enumerate(citations, start=1):
        label = f"[S{index}] 《{item.get('short_name') or item.get('law')}》{item.get('article', '')}"
        if item.get("book"):
            label += f"（{item['book']}）"
        text = (item.get("text") or "").strip().replace("\n", " ")
        lines.append(f"{label}\n{text[:600]}")
    lines.append(
        "【引用要求】\n"
        "1. 优先使用 [S1] 这样的来源编号；也可以在正文中写《法名》第X条。\n"
        "2. 只能引用上面列出的条文；没有列出的条号不要编造。\n"
        "3. 不确定时写【待核实】，不要用肯定语气虚构法律依据。"
    )
    return "\n\n".join(lines)
