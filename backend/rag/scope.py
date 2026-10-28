"""按文书类型收窄检索范围（doc_type scoping）。

9 类文书对应的法律依据差异很大：房屋买卖合同不该召回融资租赁、公司设立
条文。这里用 chunk 上已有的 `short_name / book / chapter` 元数据在检索前
圈定候选范围，向量通道用 Chroma `where` 预过滤，BM25 用索引掩码过滤。

取舍：
- 只做「软收窄」，映射表人工维护，覆盖不足时宁可放宽；
- 民事起诉状不设范围：同一份起诉状可能基于合同、侵权或劳动争议，
  doc_type 本身决定不了法律部门，硬过滤会误杀；
- 评测始终保留未过滤的 `hybrid` 对照，避免「过滤后指标变好」掩盖召回损失。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 章节路径形如「第二分编　典型合同 / 第十三章　保证合同 / 第一节　一般规定」，
# 只取章/节标题本身，用于精确匹配（避免「租赁合同」误命中「融资租赁合同」）。
_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+[章节]\s*(.+)$")


@dataclass(frozen=True)
class Scope:
    """一条允许的检索范围；同一文书类型的多个 Scope 之间是「或」。"""

    short_names: frozenset[str] = frozenset()
    books: frozenset[str] = frozenset()
    chapters: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.short_names:
            raise ValueError("Scope 至少需要一个法名")


# 民法典合同编「通则」各章：这类规则对几乎所有合同类文书都适用。
_CONTRACT_GENERAL = frozenset({
    "一般规定",
    "合同的订立",
    "合同的效力",
    "合同的履行",
    "合同的保全",
    "合同的变更和转让",
    "合同的权利义务终止",
    "违约责任",
})


DOC_TYPE_SCOPES: dict[str, tuple[Scope, ...]] = {
    "劳动合同": (
        Scope(short_names=frozenset({"劳动合同法", "劳动法"})),
    ),
    "借条": (
        Scope(
            short_names=frozenset({"民法典"}),
            books=frozenset({"合同编"}),
            chapters=_CONTRACT_GENERAL | {"借款合同", "保证合同"},
        ),
    ),
    "租赁合同": (
        Scope(
            short_names=frozenset({"民法典"}),
            books=frozenset({"合同编"}),
            chapters=_CONTRACT_GENERAL | {"租赁合同"},
        ),
    ),
    "房屋买卖合同": (
        Scope(
            short_names=frozenset({"民法典"}),
            books=frozenset({"合同编"}),
            chapters=_CONTRACT_GENERAL | {"买卖合同"},
        ),
        # 过户、共有、不动产登记等物权条文同样是买卖合同的依据
        Scope(short_names=frozenset({"民法典"}), books=frozenset({"物权编"})),
    ),
    "离婚协议书": (
        Scope(short_names=frozenset({"民法典"}), books=frozenset({"婚姻家庭编"})),
    ),
    "股权转让协议": (
        Scope(short_names=frozenset({"公司法"})),
        Scope(
            short_names=frozenset({"民法典"}),
            books=frozenset({"合同编"}),
            chapters=_CONTRACT_GENERAL,
        ),
    ),
    "保密协议": (
        Scope(short_names=frozenset({"反不正当竞争法"})),
        Scope(short_names=frozenset({"劳动合同法"})),
        Scope(
            short_names=frozenset({"民法典"}),
            books=frozenset({"合同编"}),
            chapters=_CONTRACT_GENERAL,
        ),
    ),
    "委托代理书": (
        Scope(
            short_names=frozenset({"民法典"}),
            books=frozenset({"合同编"}),
            chapters=_CONTRACT_GENERAL | {"委托合同"},
        ),
        Scope(
            short_names=frozenset({"民法典"}),
            books=frozenset({"总则编"}),
            chapters=frozenset({"一般规定", "委托代理", "代理终止"}),
        ),
        Scope(
            short_names=frozenset({"民事诉讼法"}),
            chapters=frozenset({"诉讼代理人"}),
        ),
    ),
    # 民事起诉状不设范围：文书类型无法决定实体法部门。
}


def scope_for(doc_type: str | None) -> tuple[Scope, ...] | None:
    """返回该文书类型的范围；未知类型或未配置时不收窄。"""
    return DOC_TYPE_SCOPES.get((doc_type or "").strip())


def chapter_names(chapter: str) -> tuple[str, ...]:
    """从「第X章　标题 / 第X节　标题」中提取标题，忽略分编。"""
    names = []
    for part in (chapter or "").split(" / "):
        match = _CHAPTER_RE.match(part.strip())
        if match:
            names.append(match.group(1).strip())
    return tuple(names)


def chunk_in_scope(chunk: dict, scopes: tuple[Scope, ...] | None) -> bool:
    """chunk 是否落在任一允许范围内；scopes 为 None 表示不过滤。"""
    if scopes is None:
        return True

    short_name = chunk.get("short_name") or chunk.get("law", "")
    book = chunk.get("book", "")
    names = set(chapter_names(chunk.get("chapter", "")))

    for scope in scopes:
        if short_name not in scope.short_names:
            continue
        if scope.books and book not in scope.books:
            continue
        if scope.chapters and not (names & scope.chapters):
            continue
        return True
    return False


def chroma_where(scopes: tuple[Scope, ...] | None) -> dict | None:
    """把范围翻译成 Chroma 的 where 条件；含章节限制时仍需后置过滤。

    章节是普通字符串，Chroma 没有「包含」算子，所以这里只预过滤
    法名/分编，章节过滤由调用方在结果上做。返回 None 表示不预过滤。
    """
    if not scopes:
        return None

    clauses = []
    for scope in scopes:
        condition: dict = {"short_name": {"$in": sorted(scope.short_names)}}
        if scope.books:
            condition = {
                "$and": [
                    condition,
                    {"book": {"$in": sorted(scope.books)}},
                ]
            }
        clauses.append(condition)

    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}
