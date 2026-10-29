"""
生成质量抽检脚本。

检索评测只能回答「有没有找到法条」，不能回答「文书里有没有正确使用法条」。
这个脚本用真实生成链路（RAG Prompt → LLM → 引用抽取）跑一组代表性文书，
并统计三类可机械验证的指标：

1. 检索命中：标注法条是否出现在本次检索的 Top-5；
2. 引用可追溯：正文显式写出的《法名》第X条是否存在于本地语料；
3. 引用落地：正文引用（含 [S1] 标记）是否真的来自本次检索上下文，
   而不是模型凭记忆写出、「碰巧」在语料里也存在。

用法（在 backend 目录下）：
    python -m evaluation.run_generation_eval
    python -m evaluation.run_generation_eval --limit 3
    python -m evaluation.run_generation_eval --temperature 0 --max-tokens 1200
    # 复用已有报告的生成文本，只重算引用指标（不重新调用模型）
    python -m evaluation.run_generation_eval --rescore reports/generation_eval_xxx.json
    # 额外做引用语义判卷（LLM-as-judge，会增加耗时）
    python -m evaluation.run_generation_eval --rescore reports/generation_eval_xxx.json --semantic

输出：
    evaluation/reports/generation_eval_<timestamp>.json
    evaluation/reports/generation_eval_<timestamp>.md

人工复核（可选）：
    evaluation/generation_review.json，按样本 id 记录 content_hash、citation_support
    与 notes；脚本只采纳 content_hash 与本次生成文本一致的记录，避免重跑后
    沿用旧结论。citation_support 取值：
    supported / partial / unsupported / hallucinated / no_citation。

边界：
- 自动指标只验证「引用是否可追溯/是否来自上下文」，不验证法条是否适用于具体案情；
- 生成结果受模型与采样温度影响，这是一次抽检，不是稳定的线上准确率。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# main.py 导入时会校验 SECRET_KEY；这里只借用 Prompt / 引用逻辑，不启动服务。
os.environ.setdefault("SECRET_KEY", "generation-eval-secret-key-32-bytes")

from evaluation.metrics import percentile  # noqa: E402
from llm import get_judge_client, get_llm_client, get_model_name  # noqa: E402
from rag.citations import (  # noqa: E402
    build_corpus_lookup,
    citation_key,
    extract_citations,
    verify_citations,
)
from rag.knowledge_base import chunk_documents, load_legal_documents  # noqa: E402
from rag.scope import scope_for  # noqa: E402
from rag.semantic import (  # noqa: E402
    VERDICTS,
    build_corpus_chunk_lookup,
    build_corpus_text_lookup,
    extract_citation_units,
    judge_citations,
    summarize_judgements,
)
import main as app_main  # noqa: E402

DEFAULT_SAMPLES = Path(__file__).parent / "generation_samples.jsonl"
DEFAULT_REVIEW = Path(__file__).parent / "generation_review.json"
REPORT_DIR = Path(__file__).parent / "reports"

_MARKER_RE = re.compile(r"\[S(\d+)\]")
_WHITESPACE_RE = re.compile(r"\s+")
# 多值字段（「技术方案、源代码、客户名单」）按分隔符拆开逐项匹配；
# 币种写法（人民币/¥）属于格式差异，不应当算字段丢失。
_FIELD_SPLIT_RE = re.compile(r"[、，,；;]")
_CURRENCY_RE = re.compile(r"人民币|￥|¥")

REVIEW_CATEGORIES = (
    "supported",
    "partial",
    "unsupported",
    "hallucinated",
    "no_citation",
)


def load_samples(path: Path) -> list[dict]:
    samples: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"样本第 {line_number} 行不是合法 JSON: {exc}")
            if not item.get("id") or not item.get("doc_type") or not item.get("fields"):
                raise SystemExit(f"样本第 {line_number} 行缺少 id/doc_type/fields")
            samples.append(item)
    return samples


def _select_samples(samples: list[dict], only: str) -> list[dict]:
    """按逗号分隔的 id 过滤样本，方便只复跑个别失败案例。"""
    wanted = {item.strip() for item in (only or "").split(",") if item.strip()}
    if not wanted:
        return samples
    selected = [sample for sample in samples if sample.get("id") in wanted]
    missing = wanted - {sample.get("id") for sample in selected}
    if missing:
        print(f"⚠️ 未找到样本：{', '.join(sorted(missing))}")
    return selected


def load_review(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"人工复核文件读取失败: {exc}")
    if not isinstance(data, dict):
        raise SystemExit("人工复核文件必须是 {样本 id: {citation_support, notes}} 对象")
    return data


def content_hash(content: str) -> str:
    """生成文本的指纹：人工复核只对当时那份文本有效。"""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def filter_review(results: list[dict], review: dict) -> tuple[dict, int]:
    """只保留与本次生成文本一致的人工复核记录，返回（有效记录, 失效条数）。

    模型温度不为 0，重跑后文本会变；沿用旧的 supported/partial 判断会让人
    误以为新文本也经过复核，所以用 content_hash 绑定复核对象。
    """
    by_id = {item["id"]: item for item in results}
    details = {}
    stale = 0
    for sample_id, record in review.items():
        if not isinstance(record, dict):
            continue
        item = by_id.get(sample_id)
        if item is None:
            continue
        if record.get("content_hash") != content_hash(item.get("content", "")):
            stale += 1
            continue
        details[sample_id] = record
    return details, stale


def _key_text(key: tuple[str, int, str]) -> str:
    law, number, suffix = key
    return f"{law}#{number}{suffix}"


def _normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub("", str(value or ""))


def _normalize_field_text(value: str) -> str:
    return _CURRENCY_RE.sub("", _normalize_text(value))


def field_coverage(fields: dict, content: str) -> dict:
    """检查较短的表单值是否原样出现在文书里。

    长段落（诉求、事实理由）允许模型改写，不纳入字面匹配；只看姓名、金额、
    期限、地址这类应当被准确保留的短字段。多值字段按分隔符拆开逐项匹配，
    「人民币」等币种写法视为格式差异。

    这仍是字面检查：把「50000 元」改写成「伍万元整」、把日期改写成中文格式
    都会计为未保留，因此本指标是下限而非准确率。
    """
    checked: list[str] = []
    missing: list[dict] = []
    normalized_content = _normalize_field_text(content)
    for name, value in (fields or {}).items():
        normalized_value = _normalize_text(value)
        if not normalized_value or len(normalized_value) > 30:
            continue
        checked.append(name)
        segments = [
            segment for segment in _FIELD_SPLIT_RE.split(normalized_value) if segment
        ]
        if not all(
            _normalize_field_text(segment) in normalized_content
            for segment in segments
        ):
            missing.append({"field": name, "value": str(value)})

    matched = len(checked) - len(missing)
    return {
        "checked": len(checked),
        "matched": matched,
        "rate": (matched / len(checked)) if checked else None,
        "missing": missing,
    }


def score_generation(content: str, retrieval: dict, lookup: dict, fields: dict | None = None) -> dict:
    """计算单条生成结果的引用与字段落地指标（纯函数，便于单元测试）。"""
    retrieved = retrieval.get("citations") or []
    retrieved_items = []
    retrieved_keys: set[tuple[str, int, str]] = set()
    for item in retrieved:
        law = item.get("short_name") or item.get("law") or ""
        article = item.get("article") or ""
        if not law or not article:
            continue
        key = citation_key(law, article)
        retrieved_keys.add(key)
        retrieved_items.append(
            {
                "id": item.get("id"),
                "law": law,
                "article": article,
                "key": _key_text(key),
            }
        )

    explicit = extract_citations(content)
    explicit_records: list[dict] = []
    explicit_keys: set[tuple[str, int, str]] = set()
    for item in explicit:
        key = citation_key(item["law"], item["article"])
        explicit_keys.add(key)
        explicit_records.append(
            {
                "raw": item["raw"],
                "law": item["law"],
                "article": item["article"],
                "key": _key_text(key),
                "in_corpus": key in lookup,
                "in_retrieved": key in retrieved_keys,
            }
        )

    marker_ids = sorted({int(value) for value in _MARKER_RE.findall(content or "")})
    valid_marker_ids = [index for index in marker_ids if 1 <= index <= len(retrieved)]
    invalid_marker_ids = [index for index in marker_ids if not 1 <= index <= len(retrieved)]
    marker_keys = {
        citation_key(
            retrieved[index - 1].get("short_name") or retrieved[index - 1].get("law") or "",
            retrieved[index - 1].get("article") or "",
        )
        for index in valid_marker_ids
    }
    marker_keys.discard(("", 0, ""))

    all_keys = explicit_keys | marker_keys
    valid_keys = {key for key in all_keys if key in lookup}
    grounded_keys = all_keys & retrieved_keys
    hallucinated_keys = all_keys - valid_keys
    external_valid_keys = valid_keys - retrieved_keys

    total = len(all_keys)
    return {
        "retrieved": retrieved_items,
        "expected_hit": None,  # 由调用方结合样本标注填写
        "explicit": explicit_records,
        "markers": {
            "ids": marker_ids,
            "valid": valid_marker_ids,
            "invalid": invalid_marker_ids,
        },
        "invalid": [
            {
                "kind": "explicit",
                "raw": item["raw"],
                "key": item["key"],
                "reason": "本地语料中不存在该法规/条号",
            }
            for item in explicit_records
            if not item["in_corpus"]
        ]
        + [
            {
                "kind": "marker",
                "raw": f"[S{index}]",
                "key": None,
                "reason": f"检索上下文只有 {len(retrieved)} 条，标记越界",
            }
            for index in invalid_marker_ids
        ],
        "cited_total": total,
        "cited_valid": len(valid_keys),
        "cited_grounded": len(grounded_keys),
        "cited_hallucinated": len(hallucinated_keys),
        "cited_external_valid": len(external_valid_keys),
        "traceability_rate": (len(valid_keys) / total) if total else None,
        "grounding_rate": (len(grounded_keys) / total) if total else None,
        "grounded_keys": sorted(_key_text(key) for key in grounded_keys),
        "hallucinated_keys": sorted(_key_text(key) for key in hallucinated_keys),
        "external_valid_keys": sorted(_key_text(key) for key in external_valid_keys),
        "field_coverage": field_coverage(fields or {}, content),
    }


def _expected_keys(sample: dict) -> set[tuple[str, int, str]]:
    return {
        citation_key(item["law"], item["article"])
        for item in sample.get("expected") or []
    }


def _aggregate(results: list[dict]) -> dict:
    ok = [item for item in results if not item.get("error")]
    totals = {
        "cited_total": sum(item["metrics"]["cited_total"] for item in ok),
        "cited_valid": sum(item["metrics"]["cited_valid"] for item in ok),
        "cited_grounded": sum(item["metrics"]["cited_grounded"] for item in ok),
        "cited_hallucinated": sum(item["metrics"]["cited_hallucinated"] for item in ok),
        "cited_external_valid": sum(item["metrics"]["cited_external_valid"] for item in ok),
    }
    checked_fields = sum(item["metrics"]["field_coverage"]["checked"] for item in ok)
    matched_fields = sum(item["metrics"]["field_coverage"]["matched"] for item in ok)
    retrieval_latencies = [item["latency"]["retrieval_ms"] for item in ok]
    generation_latencies = [item["latency"]["generation_ms"] for item in ok]

    return {
        "samples": len(results),
        "succeeded": len(ok),
        "failed": len(results) - len(ok),
        "retrieval_hit_samples": sum(1 for item in ok if item["retrieval"]["expected_hit"]),
        "retrieval_hit_rate": (
            sum(1 for item in ok if item["retrieval"]["expected_hit"]) / len(ok)
            if ok
            else None
        ),
        "samples_with_citations": sum(1 for item in ok if item["metrics"]["cited_total"]),
        "samples_with_markers": sum(
            1 for item in ok if item["metrics"]["markers"]["valid"]
        ),
        "samples_with_hallucination": sum(
            1 for item in ok if item["metrics"]["cited_hallucinated"]
        ),
        "samples_with_external_valid": sum(
            1 for item in ok if item["metrics"]["cited_external_valid"]
        ),
        **totals,
        "traceability_rate": (
            totals["cited_valid"] / totals["cited_total"]
            if totals["cited_total"]
            else None
        ),
        "grounding_rate": (
            totals["cited_grounded"] / totals["cited_total"]
            if totals["cited_total"]
            else None
        ),
        "field_coverage_rate": (
            matched_fields / checked_fields if checked_fields else None
        ),
        "avg_content_chars": (
            round(statistics.mean(len(item["content"]) for item in ok), 1) if ok else 0
        ),
        "retrieval_latency_p50_ms": round(percentile(retrieval_latencies, 50), 1),
        "retrieval_latency_p95_ms": round(percentile(retrieval_latencies, 95), 1),
        "generation_latency_p50_ms": round(percentile(generation_latencies, 50), 1),
        "generation_latency_p95_ms": round(percentile(generation_latencies, 95), 1),
    }


def _review_summary(results: list[dict], review: dict) -> dict:
    counts = {category: 0 for category in REVIEW_CATEGORIES}
    reviewed = 0
    for item in results:
        record = review.get(item["id"])
        if not record:
            continue
        reviewed += 1
        category = record.get("citation_support")
        if category in counts:
            counts[category] += 1
    with_citations = reviewed - counts["no_citation"]
    return {
        "reviewed": reviewed,
        "counts": counts,
        "supported_rate": (
            counts["supported"] / with_citations if with_citations else None
        ),
    }


async def annotate_semantics(
    results: list[dict],
    corpus_texts: dict,
    client,
    concurrency: int = 2,
    corpus_chunks: dict | None = None,
) -> None:
    """对每条生成结果做引用语义判卷，结果写回 ``item["semantic"]``。

    判卷失败不会中断评测：单条引用会降级为 unverifiable，并在报告里列出错误。
    """
    for position, item in enumerate(results, start=1):
        if item.get("error") or not item.get("content"):
            continue
        retrieved = (item.get("retrieval") or {}).get("citations") or []
        units = extract_citation_units(
            item["content"],
            retrieved,
            corpus_texts,
            scopes=scope_for(item.get("doc_type")),
            corpus_chunks=corpus_chunks,
        )
        print(
            f"  [semantic {position}/{len(results)}] {item['id']}: "
            f"{len(units)} 处引用待判"
        )
        judgements = await judge_citations(
            units,
            item.get("doc_type") or "",
            item.get("fields") or {},
            client,
            concurrency=concurrency,
        )
        item["semantic"] = {
            "summary": summarize_judgements(judgements),
            "units": [
                {
                    "key": judgement.get("key"),
                    "law": judgement.get("law"),
                    "article": judgement.get("article"),
                    "kind": judgement.get("kind"),
                    "claim": judgement.get("claim", ""),
                    "verdict": judgement.get("verdict"),
                    "judge_verdict": judgement.get("judge_verdict", judgement.get("verdict")),
                    "scope_mismatch": judgement.get("scope_mismatch", False),
                    "reason": judgement.get("reason", ""),
                    "context": judgement.get("context", ""),
                    "occurrences": judgement.get("occurrences", 1),
                    "source": judgement.get("source", ""),
                    "article_text": (judgement.get("article_text") or "")[:600],
                    "judge_model": judgement.get("judge_model", ""),
                    "judge_latency_ms": judgement.get("judge_latency_ms"),
                    "error": judgement.get("error"),
                }
                for judgement in judgements
            ],
        }


def _semantic_aggregate(results: list[dict]) -> dict:
    items = [item for item in results if item.get("semantic")]
    counts = {name: 0 for name in VERDICTS}
    for item in items:
        for name, value in item["semantic"]["summary"]["counts"].items():
            counts[name] = counts.get(name, 0) + value

    scored = sum(counts[name] for name in ("supported", "partial", "unsupported"))
    document_verdicts: dict[str, int] = {}
    for item in items:
        verdict = item["semantic"]["summary"]["document_verdict"]
        document_verdicts[verdict] = document_verdicts.get(verdict, 0) + 1

    return {
        "samples": len(items),
        "checked": sum(counts.values()),
        "scored": scored,
        "counts": counts,
        "supported_rate": (counts["supported"] / scored) if scored else None,
        "samples_with_unsupported": sum(
            1 for item in items
            if item["semantic"]["summary"]["counts"]["unsupported"]
        ),
        "samples_with_review_flags": sum(
            1 for item in items if item["semantic"]["summary"]["needs_review"]
        ),
        "document_verdicts": document_verdicts,
        "judge_errors": sum(
            len(item["semantic"]["summary"]["errors"]) for item in items
        ),
    }


def _semantic_review_agreement(
    results: list[dict],
    review_details: dict,
) -> dict:
    """比较 LLM 文档级判卷结论与人工复核结论。

    人工复核是文档级的（supported/partial/unsupported/no_citation），
    所以这里也把逐条判定收敛成文档级结论再比；粒度不一致本身就是一个
    限制，报告里必须写清楚，一致性只能当参考信号。
    """
    pairs = []
    for item in results:
        record = review_details.get(item["id"])
        semantic = item.get("semantic")
        if not record or not semantic:
            continue
        human = record.get("citation_support")
        if human not in ("supported", "partial", "unsupported", "no_citation"):
            continue
        judge = semantic["summary"]["document_verdict"]
        pairs.append({
            "id": item["id"],
            "human": human,
            "judge": judge,
            "agree": human == judge,
            "counts": semantic["summary"]["counts"],
        })

    exact = sum(1 for pair in pairs if pair["agree"])
    return {
        "compared": len(pairs),
        "exact": exact,
        "exact_rate": (exact / len(pairs)) if pairs else None,
        # 最危险的方向：人工认为引用不恰当，自动判卷却说支持
        "human_unsupported_judge_supported": [
            pair["id"] for pair in pairs
            if pair["human"] == "unsupported" and pair["judge"] == "supported"
        ],
        "pairs": pairs,
    }


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(payload: dict) -> str:
    aggregate = payload["aggregate"]
    review = payload["review"]
    semantic = payload.get("semantic") or {}
    lines = [
        "# 生成质量抽检报告",
        "",
        f"- 时间：{payload['generated_at']}",
        f"- 模型：{payload['config']['model']}（temperature={payload['config']['temperature']}，"
        f"max_tokens={payload['config']['max_tokens']}）",
        f"- 样本：{aggregate['succeeded']}/{aggregate['samples']} 条成功",
        "- 链路：RAG Prompt（Top-5 法条）→ LLM 生成 → 引用抽取与落地校验",
    ]
    if payload["config"].get("rescored_from"):
        lines.append(
            f"- 重评分来源：`{payload['config']['rescored_from']}`"
            "（复用生成文本，未重新调用模型）"
        )
    lines.extend([
        "",
        "## 汇总",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 检索命中率（标注法条在 Top-5） | {_pct(aggregate['retrieval_hit_rate'])} |",
        f"| 有引用的样本 | {aggregate['samples_with_citations']}/{aggregate['succeeded']} |",
        f"| 使用 [S#] 标记的样本 | {aggregate['samples_with_markers']}/{aggregate['succeeded']} |",
        f"| 引用总数（去重） | {aggregate['cited_total']} |",
        f"| 引用可追溯（条号存在于本地语料） | {aggregate['cited_valid']}/{aggregate['cited_total']}（{_pct(aggregate['traceability_rate'])}） |",
        f"| 引用落地（来自本次检索上下文） | {aggregate['cited_grounded']}/{aggregate['cited_total']}（{_pct(aggregate['grounding_rate'])}） |",
        f"| 语料外引用（编造条号） | {aggregate['cited_hallucinated']} |",
        f"| 语料内但不在上下文 | {aggregate['cited_external_valid']} |",
        f"| 关键字段原样保留率 | {_pct(aggregate['field_coverage_rate'])} |",
        f"| 平均文书长度 | {aggregate['avg_content_chars']} 字 |",
        f"| 检索延迟 P50 / P95 | {aggregate['retrieval_latency_p50_ms']} / {aggregate['retrieval_latency_p95_ms']} ms |",
        f"| 生成延迟 P50 / P95 | {aggregate['generation_latency_p50_ms']:.0f} / {aggregate['generation_latency_p95_ms']:.0f} ms |",
    ])
    if semantic.get("samples"):
        counts = semantic["counts"]
        lines.extend([
            f"| 语义判卷条目（支持/部分/不支持/无法判断） | "
            f"{counts['supported']} / {counts['partial']} / "
            f"{counts['unsupported']} / {counts['unverifiable']} |",
            f"| 语义判卷支持率（supported / 可判条目） | "
            f"{_pct(semantic['supported_rate'])}（{semantic['scored']} 条可判） |",
            f"| 至少一处 unsupported 的样本 | "
            f"{semantic['samples_with_unsupported']}/{semantic['samples']} |",
        ])
    lines.extend([
        "",
        "## 逐条结果",
        "",
        "| 样本 | 文书 | 检索命中 | 引用数 | 可追溯 | 落地 | 编造 | [S#] | 语义结论 | 字段保留 | 长度 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ])

    for item in payload["samples"]:
        if item.get("error"):
            lines.append(
                f"| `{item['id']}` | {item['doc_type']} | — | — | — | — | — | — | — | — | — |"
            )
            continue
        metrics = item["metrics"]
        coverage = metrics["field_coverage"]
        sem = item.get("semantic")
        if sem:
            counts = sem["summary"]["counts"]
            semantic_cell = (
                f"{sem['summary']['document_verdict']}"
                f"（{counts['supported']}/{counts['partial']}/{counts['unsupported']}）"
            )
        else:
            semantic_cell = "—"
        lines.append(
            f"| `{item['id']}` | {item['doc_type']} | "
            f"{'✅' if item['retrieval']['expected_hit'] else '❌'} | "
            f"{metrics['cited_total']} | {metrics['cited_valid']} | "
            f"{metrics['cited_grounded']} | {metrics['cited_hallucinated']} | "
            f"{len(metrics['markers']['valid'])} | "
            f"{semantic_cell} | "
            f"{coverage['matched']}/{coverage['checked']} | "
            f"{len(item['content'])} |"
        )

    lines.extend(["", "## 需要人工关注", ""])
    problems = []
    for item in payload["samples"]:
        if item.get("error"):
            problems.append(f"- `{item['id']}` 生成失败：{item['error']}")
            continue
        metrics = item["metrics"]
        if metrics["cited_hallucinated"]:
            problems.append(
                f"- `{item['id']}` 出现语料外引用：{', '.join(metrics['hallucinated_keys'])}"
            )
        if metrics["cited_external_valid"]:
            problems.append(
                f"- `{item['id']}` 引用了语料内但不在本次上下文中的条文："
                f"{', '.join(metrics['external_valid_keys'])}"
            )
        if not metrics["cited_total"]:
            problems.append(f"- `{item['id']}` 正文没有可识别的法条引用")
        missing = metrics["field_coverage"]["missing"]
        if missing:
            problems.append(
                f"- `{item['id']}` 字段未原样出现："
                + "，".join(f"{entry['field']}={entry['value']}" for entry in missing)
            )
    lines.extend(problems or ["- 无"])
    lines.append("")

    if review["reviewed"]:
        lines.extend(
            [
                "## 人工复核",
                "",
                f"已复核 {review['reviewed']} 条；有引用样本中 `supported` 占 {_pct(review['supported_rate'])}。",
                "",
                "| 判断 | 数量 |",
                "|---|---:|",
            ]
        )
        for category in REVIEW_CATEGORIES:
            lines.append(f"| {category} | {review['counts'][category]} |")
        lines.extend(["", "| 样本 | 判断 | 说明 |", "|---|---|---|"])
        for item in payload["samples"]:
            record = payload["review_details"].get(item["id"])
            if not record:
                continue
            lines.append(
                f"| `{item['id']}` | {record.get('citation_support', '—')} | "
                f"{_cell(record.get('notes', ''))} |"
            )
        lines.append("")

    if semantic.get("samples"):
        counts = semantic["counts"]
        judge_model = (payload["config"].get("semantic_judge") or {}).get("model") or "—"
        lines.extend([
            "## 引用语义复核（LLM 判卷）",
            "",
            f"判卷模型：`{judge_model}`；共判卷 {semantic['checked']} 处引用，"
            f"其中 {semantic['scored']} 处可判（unverifiable 不计入支持率分母）。",
            "",
            "| 判定 | 数量 | 含义 |",
            "|---|---:|---|",
            f"| supported | {counts['supported']} | 条文直接支持句子中的主张 |",
            f"| partial | {counts['partial']} | 部分相关，但扩大范围或只覆盖一部分 |",
            f"| unsupported | {counts['unsupported']} | 与主张无关或适用于不同法律关系 |",
            f"| unverifiable | {counts['unverifiable']} | 上下文没有主张、条文缺失或判卷失败 |",
            "",
            "| 样本 | 文档级结论 | 支持 | 部分 | 不支持 | 无法判断 |",
            "|---|---|---:|---:|---:|---:|",
        ])
        for item in payload["samples"]:
            sem_item = item.get("semantic")
            if not sem_item:
                continue
            summary = sem_item["summary"]
            item_counts = summary["counts"]
            lines.append(
                f"| `{item['id']}` | {summary['document_verdict']} | "
                f"{item_counts['supported']} | {item_counts['partial']} | "
                f"{item_counts['unsupported']} | {item_counts['unverifiable']} |"
            )

        details = []
        for item in payload["samples"]:
            sem_item = item.get("semantic")
            if not sem_item:
                continue
            flags = sem_item["summary"]["needs_review"]
            if not flags:
                continue
            details.append(f"\n**`{item['id']}` 需关注引用**\n")
            for flag in flags:
                claim = f"（引用意图：{_cell(flag['claim'])}）" if flag.get("claim") else ""
                scope = "（章节范围错位）" if flag.get("scope_mismatch") else ""
                details.append(
                    f"- {flag['law']}{flag['article']}：{flag['verdict']}{scope}{claim} —— "
                    f"{_cell(flag['reason'])}"
                )
        if details:
            lines.extend(["", *details, ""])
        if semantic["judge_errors"]:
            lines.append(
                f"- 有 {semantic['judge_errors']} 处引用判卷调用失败，已记为 unverifiable。"
            )
            lines.append("")

    agreement = payload.get("semantic_review") or {}
    if agreement.get("compared"):
        lines.extend([
            "## 与人工复核的一致性（文档级）",
            "",
            f"可比较 {agreement['compared']} 条，完全一致 {agreement['exact']} 条"
            f"（{_pct(agreement['exact_rate'])}）。文档级结论的收敛规则："
            "全部 supported → supported，全部 unsupported → unsupported，"
            "其余有可判条目 → partial。人工复核是按整份文书给的结论，"
            "粒度不同，一致性只能当参考信号，不是准确率。",
            "",
            "| 样本 | 人工复核 | LLM 判卷 | 一致 | 逐条 支持/部分/不支持/无法判断 |",
            "|---|---|---|---|---|",
        ])
        for pair in agreement["pairs"]:
            item_counts = pair["counts"]
            lines.append(
                f"| `{pair['id']}` | {pair['human']} | {pair['judge']} | "
                f"{'✅' if pair['agree'] else '❌'} | "
                f"{item_counts['supported']}/{item_counts['partial']}/"
                f"{item_counts['unsupported']}/{item_counts['unverifiable']} |"
            )
        if agreement["human_unsupported_judge_supported"]:
            lines.extend([
                "",
                "最危险的不一致：人工判为 unsupported、判卷却判为 supported 的样本："
                + "、".join(
                    f"`{name}`"
                    for name in agreement["human_unsupported_judge_supported"]
                )
                + "。这类漏报比误报更需要人工复核。",
            ])
        lines.append("")

    lines.extend(
        [
            "## 说明与边界",
            "",
            "- **可追溯**只表示条号真实存在于本地语料；**落地**才表示该条文确实出现在本次检索上下文里。",
            "- `[S#]` 标记按检索结果顺序映射；没有写《法名》第X条、只写 `[S1]` 的引用也会计入。",
            "- **语义判卷**是 LLM 对「句子主张 vs 条文文本」的判定，不是法律意见，也不判断条文是否已修订/废止。",
            "- 默认判卷模型与生成模型相同，存在「自己判自己」的宽松偏差；可用 `JUDGE_PROVIDER` / `JUDGE_MODEL` 换成更强的模型复验。",
            "- 生成温度不为 0，结果会波动；本报告是一次抽检，不是线上准确率承诺。",
            "- 完整生成文本见同名 JSON 报告。",
        ]
    )
    if not semantic.get("samples"):
        lines.append(
            "- 未开启语义判卷：自动指标不判断法条是否适用于具体案情，"
            "也不判断模型是否曲解条文；这部分靠人工复核。"
        )
    if payload.get("review_stale"):
        lines.append(
            f"- 有 {payload['review_stale']} 条人工复核记录因生成文本已变化被忽略"
            "（复核只对当时的文本有效）。"
        )
    lines.append("")
    return "\n".join(lines)


def evaluate_content(
    sample: dict,
    content: str,
    retrieval: dict,
    lookup: dict,
    chunks: list[dict],
) -> dict:
    """把一次生成结果转成报告需要的指标与检索明细。"""
    metrics = score_generation(content, retrieval, lookup, sample.get("fields"))
    expected_keys = _expected_keys(sample)
    retrieved_keys = {
        citation_key(
            item.get("short_name") or item.get("law") or "",
            item.get("article") or "",
        )
        for item in retrieval.get("citations") or []
    }
    hit_keys = expected_keys & retrieved_keys
    return {
        "content": content,
        "metrics": metrics,
        "retrieval": {
            "config": (retrieval.get("retrieval") or {}).get("config", ""),
            "expected_hit": bool(hit_keys),
            "hit_expected_keys": sorted(_key_text(key) for key in hit_keys),
            "expected_keys": sorted(_key_text(key) for key in expected_keys),
            "citations": [
                {
                    "id": item.get("id"),
                    "law": item.get("short_name") or item.get("law"),
                    "article": item.get("article"),
                    "text": (item.get("text") or "")[:300],
                }
                for item in retrieval.get("citations") or []
            ],
        },
        "api_citation_check": verify_citations(content, chunks),
    }


def rescore_samples(samples: list[dict], lookup: dict, chunks: list[dict]) -> list[dict]:
    """复用已生成文本，只按当前引用抽取逻辑重算指标。

    生成一次要几分钟，而引用抽取规则可能迭代。重评分让「同一批生成结果」
    可以反复对照，不会被新的随机采样掩盖。
    """
    results: list[dict] = []
    for sample in copy.deepcopy(samples):
        if sample.get("error") or not sample.get("content"):
            results.append(sample)
            continue
        retrieval = {
            "citations": [
                {**item, "short_name": item.get("short_name") or item.get("law")}
                for item in (sample.get("retrieval") or {}).get("citations") or []
            ],
            "retrieval": {"config": (sample.get("retrieval") or {}).get("config", "")},
        }
        sample.update(
            evaluate_content(
                sample,
                sample["content"],
                retrieval,
                lookup,
                chunks,
            )
        )
        results.append(sample)
    return results


def write_report(payload: dict, prefix: str = "generation_eval") -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"{prefix}_{timestamp}.json"
    md_path = REPORT_DIR / f"{prefix}_{timestamp}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _build_payload(results: list[dict], config: dict, review_path: Path) -> dict:
    review_details, stale_review = filter_review(
        results, load_review(review_path)
    )
    semantic = _semantic_aggregate(results)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": config,
        "aggregate": _aggregate(results),
        "review": _review_summary(results, review_details),
        "review_stale": stale_review,
        "review_details": review_details,
        "semantic": semantic,
        "samples": results,
    }
    if semantic["samples"]:
        payload["semantic_review"] = _semantic_review_agreement(
            results, review_details
        )
    return payload


def _print_summary(payload: dict, md_path: Path) -> None:
    aggregate = payload["aggregate"]
    print("\n=== 汇总 ===")
    print(f"样本：{aggregate['succeeded']}/{aggregate['samples']} 成功")
    print(f"检索命中率：{_pct(aggregate['retrieval_hit_rate'])}")
    print(
        f"引用可追溯：{aggregate['cited_valid']}/{aggregate['cited_total']}"
        f"（{_pct(aggregate['traceability_rate'])}）"
    )
    print(
        f"引用落地：{aggregate['cited_grounded']}/{aggregate['cited_total']}"
        f"（{_pct(aggregate['grounding_rate'])}）"
    )
    print(f"语料外引用：{aggregate['cited_hallucinated']}")
    semantic = payload.get("semantic") or {}
    if semantic.get("samples"):
        counts = semantic["counts"]
        print(
            "语义判卷："
            f"支持 {counts['supported']} / 部分 {counts['partial']} / "
            f"不支持 {counts['unsupported']} / 无法判断 {counts['unverifiable']}"
        )
        print(f"判卷支持率：{_pct(semantic['supported_rate'])}")
        agreement = payload.get("semantic_review") or {}
        if agreement.get("compared"):
            print(
                f"与人工复核一致：{agreement['exact']}/{agreement['compared']}"
                f"（{_pct(agreement['exact_rate'])}）"
            )
    print(f"报告：{md_path}")


async def run(args) -> int:
    chunks = chunk_documents(load_legal_documents())
    lookup = build_corpus_lookup(chunks)
    corpus_texts = build_corpus_text_lookup(chunks)
    corpus_chunks = build_corpus_chunk_lookup(chunks)

    judge_client = get_judge_client() if args.semantic else None
    semantic_config = {
        "enabled": bool(args.semantic),
        "model": getattr(judge_client, "model", None),
        "concurrency": args.judge_concurrency,
    }

    if args.rescore:
        source = Path(args.rescore)
        source_payload = json.loads(source.read_text(encoding="utf-8"))
        source_samples = source_payload.get("samples") or []
        source_samples = _select_samples(source_samples, args.only)
        if args.limit:
            source_samples = source_samples[: args.limit]
        results = rescore_samples(source_samples, lookup, chunks)
        if judge_client is not None:
            await annotate_semantics(
                results,
                corpus_texts,
                judge_client,
                args.judge_concurrency,
                corpus_chunks,
            )
        config = dict(source_payload.get("config") or {})
        config["rescored_from"] = str(source)
        config["semantic_judge"] = semantic_config
        payload = _build_payload(results, config, Path(args.review))
        _, md_path = write_report(payload, prefix="generation_rescore")
        _print_summary(payload, md_path)
        return 0 if payload["aggregate"]["failed"] == 0 else 1

    samples = _select_samples(load_samples(Path(args.samples)), args.only)
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        raise SystemExit("没有可运行的样本")

    client = get_llm_client()
    model = get_model_name()

    results: list[dict] = []
    for position, sample in enumerate(samples, start=1):
        print(f"[{position}/{len(samples)}] {sample['id']} ({sample['doc_type']})")
        started = time.perf_counter()
        result = {
            "id": sample["id"],
            "doc_type": sample["doc_type"],
            "focus": sample.get("focus", ""),
            "fields": sample["fields"],
            "expected": sample.get("expected", []),
            "error": None,
            "content": "",
            "latency": {"retrieval_ms": 0.0, "generation_ms": 0.0},
        }
        try:
            retrieval_started = time.perf_counter()
            prompt, retrieval = await app_main.build_prompt_with_rag_async(
                sample["doc_type"], sample["fields"]
            )
            result["latency"]["retrieval_ms"] = round(
                (time.perf_counter() - retrieval_started) * 1000, 1
            )

            generation_started = time.perf_counter()
            llm_result = await client.generate(
                prompt,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
            content = llm_result.text or ""
            result["latency"]["generation_ms"] = round(
                (time.perf_counter() - generation_started) * 1000, 1
            )

            result.update(evaluate_content(sample, content, retrieval, lookup, chunks))
            result["llm"] = {
                "model": llm_result.model,
                "prompt_tokens": llm_result.prompt_tokens,
                "completion_tokens": llm_result.completion_tokens,
            }
            metrics = result["metrics"]
            print(
                f"  citations={metrics['cited_total']} valid={metrics['cited_valid']} "
                f"grounded={metrics['cited_grounded']} chars={len(content)}"
            )
        except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整批评测
            result["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  ❌ {result['error']}")
        result["latency"]["total_ms"] = round(
            (time.perf_counter() - started) * 1000, 1
        )
        results.append(result)

    if judge_client is not None:
        await annotate_semantics(
            results,
            corpus_texts,
            judge_client,
            args.judge_concurrency,
            corpus_chunks,
        )

    payload = _build_payload(
        results,
        {
            "model": model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "top_k": 5,
            "samples_path": str(Path(args.samples)),
            "semantic_judge": semantic_config,
        },
        Path(args.review),
    )
    _, md_path = write_report(payload)
    _print_summary(payload, md_path)
    return 0 if payload["aggregate"]["failed"] == 0 else 1


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="LegalDocGenerator 生成质量抽检")
    parser.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    parser.add_argument("--review", default=str(DEFAULT_REVIEW))
    parser.add_argument(
        "--rescore",
        default="",
        metavar="REPORT_JSON",
        help="不重新生成，只按当前引用抽取逻辑重算已有报告的指标",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--only",
        default="",
        metavar="ID[,ID]",
        help="只跑指定样本 id（逗号分隔），便于复跑个别案例",
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="用 LLM 逐条判断引用是否被条文支持（会增加判卷调用与耗时）",
    )
    parser.add_argument(
        "--judge-concurrency",
        type=int,
        default=2,
        help="语义判卷并发数（本地单卡建议 1–2）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
