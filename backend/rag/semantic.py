"""引用语义恰当性校验（LLM-as-judge）。

机械校验（``citations.py``）只能回答两个问题：条号是否存在于本地语料、
引用是否来自本次检索上下文。它回答不了最关键的问题：

    模型把这条法条拿来支撑的那句话，条文真的支持吗？

这个模块对正文中实际出现的每一处引用，抓出它所在的句子，连同条文原文
交给一个独立的判卷 Prompt，输出 supported / partial / unsupported /
unverifiable。

边界（也很重要）：
- 这是 LLM 判定，不是法律意见；判卷模型也会错，且默认与生成模型相同，
  存在「自己判自己」的宽松偏差。可用 JUDGE_* 环境变量换成更强的模型。
- 只判断「句子中的法律主张 vs 条文文本」的对应关系，不判断案件事实真伪，
  也不判断条文是否已修订/废止。
- 句子只是罗列条号、没有可判断的主张时判 unverifiable，不计入支持率分母，
  避免把「上下文不足」冤枉成「引用错误」。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Iterable, Optional

from .citations import citation_key, find_citations, normalize_law_name
from .scope import Scope, chunk_in_scope, scope_for

VERDICTS = ("supported", "partial", "unsupported", "unverifiable")
_SCORED_VERDICTS = ("supported", "partial", "unsupported")

_MARKER_RE = re.compile(r"\[S(\d+)\]")
_WHITESPACE_RE = re.compile(r"\s+")
_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.S)
_VERDICT_FIELD_RE = re.compile(
    r'"?verdict"?\s*[:：]\s*"?(supported|partial|unsupported|unverifiable|'
    r"支持|部分支持|不支持|无法判断|无法验证)",
    re.I,
)
_REASON_FIELD_RE = re.compile(r'"?reason"?\s*[:：]\s*"([^"\n]{1,300})', re.I)
_CLAIM_FIELD_RE = re.compile(r'"?claim"?\s*[:：]\s*"([^"\n]{1,120})', re.I)
# 小模型偶尔出现「reason 说无关、verdict 却写 unverifiable」的不一致；
# 既然它已经在理由里断言无关，就按 unsupported 记账，避免把明确错配洗成「无法判断」。
_UNSUPPORTED_REASON_HINTS = ("无关", "不相关", "没有关系", "不适用于", "不同法律关系")

# 表单字段名是英文 key；给判卷模型换成中文标签，减少「landlord/tenant」
# 这类词被误读法律关系的概率。
_FIELD_LABELS = {
    "party_a": "甲方", "party_b": "乙方", "term": "期限", "salary": "工资",
    "plaintiff": "原告", "defendant": "被告", "claim": "诉讼请求",
    "reason": "事实与理由", "lender": "出借人", "borrower": "借款人",
    "amount": "金额", "repay_date": "还款日期", "landlord": "出租人",
    "tenant": "承租人", "property": "租赁物", "rent": "租金",
    "husband": "丈夫", "wife": "妻子", "child_custody": "子女抚养",
    "property_split": "财产分割", "transferor": "转让方",
    "transferee": "受让方", "company_name": "公司名称",
    "share_ratio": "股权比例", "share_price": "转让价格",
    "payment_method": "支付方式", "employer": "用人单位",
    "employee": "劳动者", "confidential_scope": "保密范围",
    "confidential_period": "保密期限", "seller": "出卖人",
    "buyer": "买受人", "house_address": "房屋地址",
    "house_area": "房屋面积", "house_price": "房屋价款",
    "payment_time": "付款时间", "client": "委托人", "agent": "受托人",
    "matter": "委托事项", "authority": "授权范围", "period": "委托期限",
}
_SENTENCE_BOUNDARIES = "。！？；\n\r"

JUDGE_SYSTEM_PROMPT = """你是法律文书的引用审核员。逐处判断【所引条文】是否支持【正文句子】中的引用意图。

判断分两层，重点是第二层：
A. 句子是否准确复述条文——这不是重点。
B. 该条文是否适用于本案的法律关系与事实——这是重点。
只有 B 成立才可判 supported。若条文属于不同法律关系（例如融资租赁 vs 普通租赁、
技术转让 vs 劳动关系、强制执行 vs 协议转让、债券转让 vs 股权转让），
即使句子复述准确，也应判 unsupported。

步骤：
1. 先概括这处引用想支持的主张（claim）。结合【正文句子】中被【引用】标出的部分和【案情/表单要点】。
   如果句子只是罗列条号或复述条文，就用案情要点概括文书的核心请求。
2. 再只依据条文原文判断：该条文是否适用于本案，并支持 claim。
3. reason 与 verdict 必须一致：如果认为条文与 claim 无关，verdict 必须是 unsupported，不能写成 unverifiable。

规则：
- 只依据给出的条文原文，不用条文之外的记忆，不补充没有写出的事实。
- 「主题相关」不等于「支持」：条文必须能直接支撑 claim 的法律结论、权利义务或责任。
- claim 的主体、触发条件、法律后果都要能在条文中找到依据；只有定义、计算标准或程序规定相同，不足以判 supported，最多 partial。
- 注意条文开头的「所属」编/章：同一部法律的不同章节调整不同法律关系。若条文所属章节与本案法律关系不符（例如融资租赁合同章 vs 普通租赁、技术转让章 vs 劳动关系），判 unsupported。
- 条文以某个前提为适用条件（如「未按照约定用途」「经催告后」「在融资租赁合同中」），而案情没有该前提时，不能判 supported；前提是必要条件时判 unsupported。
- claim 出现「均」「一律」「随时」「全部」等绝对化表述时，确认条文是否真的无条件支持；条文只规定计算标准时，不能支持「均应支付」这类义务。
- 不要因为条文和文书类型属于同一法律领域就判 supported。
- unverifiable 只用于：句子和案情都没有可判断的主张，或条文原文缺失/不足。
- 不评价文书写得好不好，也不判断案件事实真假。

判定标准：
- supported：条文直接规定或直接支撑 claim。
- partial：条文部分相关，但 claim 扩大了适用范围、混淆了适用条件，或只覆盖其中一部分。
- unsupported：条文与 claim 无关，或明显适用于不同的法律关系/情形。
- unverifiable：确实没有可判断的主张，或条文原文不足。

示例：
【句子】根据《示例法》第五条，承租人应按约定支付租金。
【条文】第五条 承租人应当按照约定的期限支付租金。
→ {"claim": "承租人应按约定支付租金", "verdict": "supported", "reason": "条文直接规定承租人应按期支付租金。"}

【句子】承租人逾期不付租金的，出租人可解除合同并没收全部押金。依据《示例法》第六条。
【条文】第六条 承租人逾期不支付租金的，出租人可以解除合同。
→ {"claim": "出租人可解除合同并没收全部押金", "verdict": "partial", "reason": "解除合同有依据，但没收全部押金超出条文范围。"}

【句子】根据《示例法》第七条，被告应支付逾期还款利息。
【条文】第七条 借款的利息不得预先在本金中扣除。
→ {"claim": "被告应支付逾期还款利息", "verdict": "unsupported", "reason": "条文规范利息预先扣除，与逾期利息无关。"}

只输出一个 JSON 对象，不要 Markdown、不要额外解释：
{"claim": "不超过 40 字", "verdict": "supported|partial|unsupported|unverifiable", "reason": "不超过 80 字"}"""

_VERDICT_ALIASES = {
    "supported": "supported",
    "support": "supported",
    "支持": "supported",
    "完全支持": "supported",
    "partial": "partial",
    "partially": "partial",
    "partially_supported": "partial",
    "部分支持": "partial",
    "部分": "partial",
    "unsupported": "unsupported",
    "not_supported": "unsupported",
    "不支持": "unsupported",
    "无关": "unsupported",
    "unverifiable": "unverifiable",
    "无法判断": "unverifiable",
    "无法验证": "unverifiable",
    "不确定": "unverifiable",
}


# ==================== 条文与上下文 ====================

def build_corpus_text_lookup(chunks: Iterable[dict]) -> dict[tuple[str, int, str], str]:
    """构建 (法规简称, 条号, 后缀) → 条文全文。

    长条文会被切块器拆成多个 chunk；``citations.build_corpus_lookup`` 只取
    第一块，判卷时可能看不到后半段，所以这里按 chunk_index 拼回全文。
    """
    grouped: dict[tuple[str, int, str], dict] = {}
    for chunk in chunks or []:
        law = chunk.get("short_name") or chunk.get("law") or ""
        article = chunk.get("article") or ""
        text = (chunk.get("text") or "").strip()
        if not law or not article or not text:
            continue
        key = citation_key(law, article)
        entry = grouped.setdefault(key, {"parts": [], "meta": set()})
        entry["parts"].append((chunk.get("chunk_index") or 0, text))
        location = " / ".join(
            part for part in (chunk.get("book"), chunk.get("chapter")) if part
        )
        if location:
            entry["meta"].add(location)

    corpus: dict[tuple[str, int, str], str] = {}
    for key, entry in grouped.items():
        body = "\n".join(text for _, text in sorted(entry["parts"]))
        # 编/章是判断「同一法律下不同法律关系」的关键：第七百五十二条的
        # 措辞很像普通租赁，但它属于融资租赁合同章。只给正文，判卷很难发现错配。
        header = "；".join(sorted(entry["meta"]))
        corpus[key] = f"（所属：{header}）\n{body}" if header else body
    return corpus


def build_corpus_chunk_lookup(chunks: Iterable[dict]) -> dict[tuple[str, int, str], dict]:
    """构建 (法规简称, 条号, 后缀) → 首个条文块，用于读取编/章元数据。"""
    lookup: dict[tuple[str, int, str], dict] = {}
    for chunk in chunks or []:
        law = chunk.get("short_name") or chunk.get("law") or ""
        article = chunk.get("article") or ""
        if not law or not article:
            continue
        lookup.setdefault(citation_key(law, article), chunk)
    return lookup


def _previous_boundary(text: str, position: int) -> int:
    return max(
        (text.rfind(char, 0, position) for char in _SENTENCE_BOUNDARIES),
        default=-1,
    )


def _next_boundary(text: str, position: int) -> int:
    found = [
        index
        for char in _SENTENCE_BOUNDARIES
        if (index := text.find(char, position)) != -1
    ]
    return min(found) if found else -1


def extract_context(
    text: str,
    start: int,
    end: int,
    max_chars: int = 400,
) -> str:
    """截取引用所在的句子；句子太短时向前补一句，帮助判卷理解主张。"""
    left = _previous_boundary(text, start) + 1
    right = _next_boundary(text, end)
    right = len(text) if right == -1 else right + 1

    # 引用句往往只是「根据《X》第Y条，……」或一张条号清单，单看一句
    # 判断不了它想支持什么。句子不算太长时，把上一句（通常是案情主张）
    # 一起带上；这样判卷才能看出「条文与该主张是否错配」。
    if right - left < 120 and left > 0:
        cursor = left - 1
        # 先跨过当前句前的换行/标点，找到上一句的最后一个字
        while cursor >= 0 and (
            text[cursor] in _SENTENCE_BOUNDARIES or text[cursor] in " \t"
        ):
            cursor -= 1
        if cursor >= 0:
            previous_left = _previous_boundary(text, cursor) + 1
            if start - previous_left <= 300:
                left = previous_left
                while left < start and text[left] in " \t\r\n":
                    left += 1

    context = text[left:right].strip()
    if len(context) <= max_chars:
        return context

    # 超长段落：以引用为中心截取，保证引用本身留在窗口内。
    offset = max(0, start - left)
    window_start = max(0, offset - max_chars // 2)
    clipped = context[window_start:window_start + max_chars].strip()
    prefix = "…" if window_start > 0 else ""
    suffix = "…" if window_start + max_chars < len(context) else ""
    return f"{prefix}{clipped}{suffix}"


def extract_citation_units(
    text: str,
    retrieved: Optional[list[dict]] = None,
    corpus_texts: Optional[dict[tuple[str, int, str], str]] = None,
    max_context_chars: int = 400,
    scopes: Optional[tuple[Scope, ...]] = None,
    corpus_chunks: Optional[dict[tuple[str, int, str], dict]] = None,
) -> list[dict]:
    """把正文中的显式引用与 ``[S#]`` 标记合并成待判卷的引用单元。

    同一部法律同一条文只判一次（第一次出现的位置），并记录出现次数。
    条文原文优先取本地语料全文，其次退回本次检索上下文（可能被截断）。
    """
    text = text or ""
    retrieved = retrieved or []
    corpus_texts = corpus_texts or {}
    units: list[dict] = []
    by_key: dict[tuple[str, int, str], dict] = {}

    def add(
        law: str,
        article: str,
        raw: str,
        start: int,
        end: int,
        kind: str,
        retrieved_text: str = "",
    ) -> None:
        key = citation_key(law, article)
        canonical_law = normalize_law_name(law)
        if key in by_key:
            by_key[key]["occurrences"] += 1
            return

        article_text = (corpus_texts.get(key) or retrieved_text or "").strip()
        chunk = (corpus_chunks or {}).get(key)
        in_scope = (
            chunk_in_scope(chunk, scopes)
            if chunk is not None and scopes is not None
            else None
        )
        unit = {
            "key": f"{key[0]}#{key[1]}{key[2]}",
            "law": canonical_law,
            "article": article,
            "kind": kind,
            "raw": raw,
            "position": start,
            "context": extract_context(text, start, end, max_context_chars),
            "article_text": article_text,
            "source": "corpus" if corpus_texts.get(key) else (
                "retrieval" if retrieved_text else "missing"
            ),
            "in_scope": in_scope,
            "scope_mismatch": in_scope is False,
            "occurrences": 1,
        }
        by_key[key] = unit
        units.append(unit)

    for item in find_citations(text):
        add(
            item["law"],
            item["article"],
            item["raw"],
            item["start"],
            item["end"],
            "explicit",
        )

    for match in _MARKER_RE.finditer(text):
        index = int(match.group(1))
        if not 1 <= index <= len(retrieved):
            continue
        item = retrieved[index - 1]
        law = item.get("short_name") or item.get("law") or ""
        article = item.get("article") or ""
        if not law or not article:
            continue
        add(
            law,
            article,
            match.group(0),
            match.start(),
            match.end(),
            "marker",
            retrieved_text=item.get("text") or "",
        )

    units.sort(key=lambda unit: unit["position"])
    return units


# ==================== 判卷 ====================

def _facts_digest(fields: Optional[dict], max_chars: int = 900) -> str:
    lines: list[str] = []
    for name, value in (fields or {}).items():
        text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
        if not text:
            continue
        if len(text) > 160:
            text = text[:160] + "…"
        lines.append(f"- {_FIELD_LABELS.get(name, name)}: {text}")
        if sum(len(line) for line in lines) >= max_chars:
            break
    return "\n".join(lines)[:max_chars]


def _highlight_context(context: str, raw: str) -> str:
    """在上下文里标出本次要判的那一处引用，避免模型看错句子。"""
    marker = (raw or "").strip("、,，和及 \t")
    if marker and marker in context:
        return context.replace(marker, f"【引用】{marker}【/引用】", 1)
    return context


def build_judge_prompt(unit: dict, doc_type: str, fields: Optional[dict]) -> str:
    facts = _facts_digest(fields)
    article_text = (unit.get("article_text") or "").strip()
    if len(article_text) > 900:
        article_text = article_text[:900] + "…"
    context = _highlight_context(unit.get("context") or "", unit.get("raw") or "")
    scope_note = ""
    if unit.get("scope_mismatch"):
        scope_note = (
            f"\n【范围提示】该条文所属章节不在文书类型「{doc_type or '未注明'}」的常用范围内。"
            "若它属于不同法律关系，应判 unsupported；只有确有必要适用且条文能直接支持 claim "
            "时才可判 supported。\n"
        )
    return f"""文书类型：{doc_type or "未注明"}

【案情/表单要点】
{facts or "（未提供）"}

【正文中包含该引用的句子】
{context or "（无法定位）"}

【被引用的条文】《{unit.get("law")}》{unit.get("article")}
{article_text or "（条文原文缺失）"}
{scope_note}
请按系统要求先概括 claim，再判断该条文是否支持它，只输出 JSON。"""


def _normalize_verdict(value: object) -> Optional[str]:
    text = _WHITESPACE_RE.sub("", str(value or "")).strip().strip('"。.,，')
    return _VERDICT_ALIASES.get(text.lower(), _VERDICT_ALIASES.get(text))


def parse_judge_response(raw: str) -> dict:
    """解析判卷输出；模型偶尔会包 Markdown 或带前后缀，这里尽量兜底。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("判卷返回为空")

    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None

    if isinstance(data, dict):
        verdict = _normalize_verdict(data.get("verdict"))
        reason = _WHITESPACE_RE.sub(" ", str(data.get("reason") or "")).strip()
        claim = _WHITESPACE_RE.sub(" ", str(data.get("claim") or "")).strip()
    else:
        verdict_match = _VERDICT_FIELD_RE.search(text)
        reason_match = _REASON_FIELD_RE.search(text)
        claim_match = _CLAIM_FIELD_RE.search(text)
        verdict = _normalize_verdict(verdict_match.group(1)) if verdict_match else None
        reason = reason_match.group(1).strip() if reason_match else ""
        claim = claim_match.group(1).strip() if claim_match else ""

    if verdict not in VERDICTS:
        raise ValueError(f"无法解析判卷结论: {text[:120]}")
    if verdict == "unverifiable" and any(
        hint in reason for hint in _UNSUPPORTED_REASON_HINTS
    ):
        verdict = "unsupported"
    return {"claim": claim[:80], "verdict": verdict, "reason": reason[:200]}


async def judge_unit(
    unit: dict,
    doc_type: str,
    fields: Optional[dict],
    client,
) -> dict:
    """判卷单个引用；任何失败都降级为 unverifiable，不中断整批生成。"""
    enriched = dict(unit)
    if not unit.get("article_text"):
        enriched.update(
            claim="",
            verdict="unverifiable",
            reason="本地语料中没有该条文原文，无法判断语义对应",
            error=None,
        )
        return enriched

    try:
        result = await client.generate(
            build_judge_prompt(unit, doc_type, fields),
            system=JUDGE_SYSTEM_PROMPT,
            temperature=0,
            max_tokens=220,
            json_mode=True,
        )
        parsed = parse_judge_response(result.text)
    except Exception as exc:  # noqa: BLE001 - 判卷失败不应影响文书生成
        detail = _WHITESPACE_RE.sub(" ", str(exc)).strip()[:80]
        enriched.update(
            claim="",
            verdict="unverifiable",
            reason=f"判卷调用失败：{type(exc).__name__}" + (f": {detail}" if detail else ""),
            error=str(exc)[:200],
        )
        return enriched

    judge_verdict = parsed["verdict"]
    verdict = judge_verdict
    reason = parsed["reason"]
    scope_mismatch = bool(unit.get("scope_mismatch"))
    if scope_mismatch and judge_verdict == "supported":
        # 编/章错位是比句面复述更强的信号：判卷模型很容易因为条文措辞相似
        # 而忽略「它属于融资租赁而不是普通租赁」。保留原判卷结论供复核，
        # 最终结论降级为 unsupported。
        verdict = "unsupported"
        reason = (
            f"章节范围错位：该条文不在文书类型常用范围内；"
            f"原判卷认为「{parsed['reason']}」"
        )[:200]

    enriched.update(
        claim=parsed["claim"],
        verdict=verdict,
        judge_verdict=judge_verdict,
        scope_mismatch=scope_mismatch,
        reason=reason,
        error=None,
        judge_model=getattr(result, "model", ""),
        judge_latency_ms=round(getattr(result, "latency_ms", 0.0), 1),
    )
    return enriched


async def judge_citations(
    units: list[dict],
    doc_type: str,
    fields: Optional[dict],
    client,
    concurrency: int = 2,
) -> list[dict]:
    """并发判卷一组引用；默认并发 2，避免本地单卡模型被请求打满。"""
    if not units:
        return []
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run(unit: dict) -> dict:
        async with semaphore:
            return await judge_unit(unit, doc_type, fields, client)

    return list(await asyncio.gather(*(run(unit) for unit in units)))


# ==================== 汇总 ====================

def document_verdict(counts: dict) -> str:
    """把逐条判定收敛成文档级结论，用于和人工复核对比。

    映射规则（报告里会写明）：
    - 全部 supported → supported
    - 全部 unsupported → unsupported
    - 其余有可判条目 → partial
    - 没有可判条目 → no_citation / unverifiable
    """
    scored = sum(counts.get(name, 0) for name in _SCORED_VERDICTS)
    if not scored:
        return (
            "no_citation"
            if not counts.get("unverifiable")
            else "unverifiable"
        )
    if counts.get("supported", 0) == scored:
        return "supported"
    if counts.get("unsupported", 0) == scored:
        return "unsupported"
    return "partial"


def summarize_judgements(judgements: list[dict]) -> dict:
    counts = {name: 0 for name in VERDICTS}
    for item in judgements:
        verdict = item.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
        else:
            counts["unverifiable"] += 1

    scored = sum(counts[name] for name in _SCORED_VERDICTS)
    needs_review = [
        {
            "key": item.get("key"),
            "law": item.get("law"),
            "article": item.get("article"),
            "claim": item.get("claim", ""),
            "verdict": item.get("verdict"),
            "judge_verdict": item.get("judge_verdict", item.get("verdict")),
            "scope_mismatch": item.get("scope_mismatch", False),
            "reason": item.get("reason", ""),
        }
        for item in judgements
        if item.get("verdict") in ("unsupported", "partial")
    ]
    return {
        "checked": len(judgements),
        "scored": scored,
        "counts": counts,
        "supported_rate": (counts["supported"] / scored) if scored else None,
        "document_verdict": document_verdict(counts),
        "needs_review": needs_review,
        "errors": [
            {
                "key": item.get("key"),
                "law": item.get("law"),
                "article": item.get("article"),
                "error": item.get("error"),
            }
            for item in judgements
            if item.get("error")
        ],
    }


def public_units(judgements: list[dict]) -> list[dict]:
    """API 响应里只保留前端展示需要的字段，避免把整段条文塞进响应。"""
    return [
        {
            "key": item.get("key"),
            "law": item.get("law"),
            "article": item.get("article"),
            "kind": item.get("kind"),
            "claim": item.get("claim", ""),
            "verdict": item.get("verdict"),
            "judge_verdict": item.get("judge_verdict", item.get("verdict")),
            "scope_mismatch": item.get("scope_mismatch", False),
            "reason": item.get("reason", ""),
            "context": item.get("context", ""),
            "occurrences": item.get("occurrences", 1),
        }
        for item in judgements
    ]


async def check_citations(
    content: str,
    retrieved: Optional[list[dict]],
    doc_type: str,
    fields: Optional[dict],
    client,
    corpus_texts: Optional[dict[tuple[str, int, str], str]] = None,
    concurrency: int = 2,
    chunks: Optional[list[dict]] = None,
) -> dict:
    """完整链路：抽引用单元 → LLM 判卷 → 汇总。

    传入 ``chunks`` 时会补齐条文全文与编/章元数据，并按文书类型做范围提示；
    不传则只用调用方给的 ``corpus_texts``（评测里通常已预先构建）。
    """
    corpus_chunks = None
    scopes = None
    if chunks:
        if corpus_texts is None:
            corpus_texts = build_corpus_text_lookup(chunks)
        corpus_chunks = build_corpus_chunk_lookup(chunks)
        scopes = scope_for(doc_type)
    units = extract_citation_units(
        content,
        retrieved,
        corpus_texts,
        scopes=scopes,
        corpus_chunks=corpus_chunks,
    )
    judgements = await judge_citations(
        units, doc_type, fields, client, concurrency=concurrency
    )
    return {
        "enabled": True,
        "units": public_units(judgements),
        "summary": summarize_judgements(judgements),
    }
