"""
RAG 检索评测脚本（消融实验）。

用法（在 backend 目录下）：
    python -m evaluation.run_eval --limit 10
    python -m evaluation.run_eval --configs dense,hybrid,hybrid+rerank
    python -m evaluation.run_eval --golden evaluation/golden_set.jsonl

输出：
    evaluation/reports/rag_eval_<timestamp>.json
    evaluation/reports/rag_eval_<timestamp>.md

指标：
- Recall@K：Top-K 覆盖了多少条标注法条
- MRR：第一条相关法条的平均倒数排名
- nDCG@K：考虑排序位置的相关性
- P50/P95 延迟：检索链路耗时（不含生成）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 允许 `python evaluation/run_eval.py` 直接运行
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evaluation.metrics import aggregate, dedupe, percentile  # noqa: E402
from rag.citations import build_corpus_lookup, citation_key  # noqa: E402
from rag.hybrid import (  # noqa: E402
    BM25_GUARANTEE,
    BM25_WEIGHT,
    RRF_K,
    fuse_candidates,
)
from rag.indexer import get_retrieval_index  # noqa: E402
from rag.knowledge_base import (  # noqa: E402
    chunk_documents,
    get_knowledge_base_stats,
    load_legal_documents,
)
from rag.query import expand_query  # noqa: E402

DEFAULT_GOLDEN = Path(__file__).parent / "golden_set.jsonl"
REPORT_DIR = Path(__file__).parent / "reports"


def load_golden(path: Path) -> list[dict]:
    items = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"golden 第 {line_number} 行不是合法 JSON: {exc}")
            if not item.get("id") or not item.get("query") or not item.get("relevant"):
                raise SystemExit(f"golden 第 {line_number} 行缺少 id/query/relevant")
            items.append(item)
    return items


def key_of_chunk(chunk: dict) -> str:
    law, number, suffix = citation_key(
        chunk.get("short_name") or chunk.get("law", ""),
        chunk.get("article", ""),
    )
    return f"{law}#{number}{suffix}"


def key_of_gold(item: dict) -> str:
    law, number, suffix = citation_key(item["law"], item["article"])
    return f"{law}#{number}{suffix}"


def relevant_keys(item: dict) -> set[str]:
    return {key_of_gold(relevant) for relevant in item["relevant"]}


def chunks_to_keys(chunks: list[dict], k: int) -> list[str]:
    return dedupe([key_of_chunk(chunk) for chunk in chunks[:k]])


def validate_golden(golden: list[dict]) -> list[str]:
    """确认金标集里的法条都真实存在于切块后的语料中。

    条号写错会让 Recall 永远为 0，必须在跑评测前拦住。
    """
    chunks = chunk_documents(load_legal_documents())
    lookup = build_corpus_lookup(chunks)
    missing = []
    for item in golden:
        for relevant in item["relevant"]:
            key = citation_key(relevant["law"], relevant["article"])
            if key not in lookup:
                missing.append(
                    f"{item['id']}: 《{relevant['law']}》{relevant['article']}"
                )
    return missing


async def evaluate(
    golden: list[dict],
    configs: list[str],
    top_k: int,
    candidate_k: int,
) -> dict:
    index = get_retrieval_index()
    from rag.rerank import llm_rerank

    all_keys: dict[str, dict[str, list[str]]] = {config: {} for config in configs}
    latencies: dict[str, list[float]] = {config: [] for config in configs}
    failures: dict[str, list[dict]] = {config: [] for config in configs}
    relevant_per_query: dict[str, set[str]] = {}

    for position, item in enumerate(golden, start=1):
        query_id = item["id"]
        original_query = item["query"]
        query = expand_query(original_query)
        print(f"[{position}/{len(golden)}] {query_id}: {original_query}")
        relevant_per_query[query_id] = relevant_keys(item)

        dense_started = time.perf_counter()
        dense = index.dense_search(query, k=candidate_k)
        dense_ms = (time.perf_counter() - dense_started) * 1000

        bm25_started = time.perf_counter()
        sparse = index.bm25_search(query, k=candidate_k)
        bm25_ms = (time.perf_counter() - bm25_started) * 1000

        fuse_started = time.perf_counter()
        hybrid = fuse_candidates(dense, sparse, top_k=top_k)
        rerank_candidates = fuse_candidates(
            dense, sparse, top_k=max(top_k, 10)
        )
        fuse_ms = (time.perf_counter() - fuse_started) * 1000
        retrieval_ms = dense_ms + bm25_ms + fuse_ms

        if "dense" in configs:
            all_keys["dense"][query_id] = chunks_to_keys(dense, top_k)
            latencies["dense"].append(dense_ms)

        if "bm25" in configs:
            all_keys["bm25"][query_id] = chunks_to_keys(sparse, top_k)
            latencies["bm25"].append(bm25_ms)

        if "hybrid" in configs:
            all_keys["hybrid"][query_id] = chunks_to_keys(hybrid, top_k)
            latencies["hybrid"].append(retrieval_ms)

        if "hybrid+scope" in configs:
            # 默认融合 + 用金标里的 doc_type 收窄法名/分编/章节
            scope_started = time.perf_counter()
            doc_type = item.get("doc_type", "")
            scoped_dense = index.dense_search(
                query, k=candidate_k, doc_type=doc_type
            )
            scoped_sparse = index.bm25_search(
                query, k=candidate_k, doc_type=doc_type
            )
            scoped = fuse_candidates(scoped_dense, scoped_sparse, top_k=top_k)
            scope_ms = (time.perf_counter() - scope_started) * 1000
            all_keys["hybrid+scope"][query_id] = chunks_to_keys(scoped, top_k)
            latencies["hybrid+scope"].append(scope_ms)

        if "hybrid+rerank" in configs:
            rerank_started = time.perf_counter()
            try:
                # 重排看用户原话，检索看扩展后的术语
                reranked = await llm_rerank(original_query, rerank_candidates, top_k=top_k)
            except Exception as exc:
                print(f"  ⚠️ 重排失败，回退到 hybrid: {exc}")
                reranked = hybrid
            all_keys["hybrid+rerank"][query_id] = chunks_to_keys(reranked, top_k)
            latencies["hybrid+rerank"].append(
                retrieval_ms + (time.perf_counter() - rerank_started) * 1000
            )

        for config in configs:
            keys = all_keys[config].get(query_id, [])
            if not set(keys) & relevant_per_query[query_id]:
                failures[config].append({
                    "id": query_id,
                    "doc_type": item.get("doc_type", ""),
                    "query": query,
                    "expected": sorted(relevant_per_query[query_id]),
                    "got": keys,
                })

    corpus = get_knowledge_base_stats()
    # 报告会进仓库/简历附件，不记录本机绝对路径
    corpus.pop("storage_path", None)

    summary = {}
    for config in configs:
        metrics = aggregate(all_keys[config], relevant_per_query, k=top_k)
        metrics["latency_p50_ms"] = round(statistics.median(latencies[config]), 1)
        metrics["latency_p95_ms"] = round(percentile(latencies[config], 95), 1)
        metrics["failures"] = failures[config]
        summary[config] = metrics

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "top_k": top_k,
        "candidate_k": candidate_k,
        "fusion": {
            "rrf_k": RRF_K,
            "bm25_weight": BM25_WEIGHT,
            "bm25_guarantee": BM25_GUARANTEE,
        },
        "corpus": corpus,
        "summary": summary,
        "per_query": {
            config: {
                query_id: {
                    "retrieved": keys,
                    "relevant": sorted(relevant_per_query[query_id]),
                }
                for query_id, keys in keys_by_query.items()
            }
            for config, keys_by_query in all_keys.items()
        },
    }


def render_markdown(result: dict) -> str:
    summary = result["summary"]
    corpus = result["corpus"]
    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 时间：{result['generated_at']}",
        f"- 语料：{len(corpus.get('laws', []))} 部法规 / {corpus.get('total_chunks', 0)} 个法条块",
        f"- Embedding：{corpus.get('embedding_model', '')}",
        f"- Top-K：{result['top_k']}，候选池：{result['candidate_k']}",
        f"- 融合参数：BM25 weight={result['fusion']['bm25_weight']}，"
        f"guarantee={result['fusion']['bm25_guarantee']}，RRF k={result['fusion']['rrf_k']}",
        "",
        "| 配置 | Recall@K | MRR | nDCG@K | P50 延迟 | P95 延迟 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for config, metrics in summary.items():
        lines.append(
            f"| {config} | {metrics['recall@' + str(result['top_k'])]:.4f} | "
            f"{metrics['mrr']:.4f} | {metrics['ndcg@' + str(result['top_k'])]:.4f} | "
            f"{metrics['latency_p50_ms']:.0f} ms | {metrics['latency_p95_ms']:.0f} ms |"
        )

    lines.extend(["", "## 失败样例（Top-K 内没有命中任何标注法条）", ""])
    for config, metrics in summary.items():
        failures = metrics.get("failures", [])
        lines.append(f"### {config}（{len(failures)} 条）")
        if not failures:
            lines.append("无。")
        for failure in failures[:10]:
            label = failure.get("doc_type") or "未分类"
            lines.append(f"- `{failure['id']}`（{label}）：{failure['query']}")
            lines.append(f"  - 期望：{', '.join(failure['expected'])}")
            lines.append(f"  - 实际：{', '.join(failure['got'])}")
        lines.append("")

    lines.extend([
        "## 说明",
        "",
        "- 指标按法条去重：同一法条被切成多个块时只计一次。",
        "- `dense` 是纯向量基线；`bm25` 是纯关键词基线。",
        "- `hybrid` 是默认配置（等权 RRF；旧 BM25 保底可用 "
        "`HYBRID_BM25_WEIGHT=2.0`、`HYBRID_BM25_GUARANTEE=4` 复现）；",
        "  `hybrid+scope` 在此基础上按金标的 doc_type 收窄法名/分编/章节；",
        "  `hybrid+rerank` 在融合候选上再做一次 LLM 列表重排。",
        "- 延迟只包含检索/重排，不包含文书生成。",
        "- 数据集规模有限，指标用于版本间对比，不代表线上效果。",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索消融评测")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--configs",
        default="dense,bm25,hybrid,hybrid+scope,hybrid+rerank",
        help="逗号分隔：dense,bm25,hybrid,hybrid+scope,hybrid+rerank",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条，0 表示全部")
    args = parser.parse_args()

    golden = load_golden(args.golden)
    if args.limit:
        golden = golden[:args.limit]

    missing = validate_golden(golden)
    if missing:
        print("❌ 以下金标法条在本地语料中不存在：", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        raise SystemExit(1)
    print(f"✅ 金标校验通过：{len(golden)} 条查询，所有标注法条均存在")

    configs = [item.strip() for item in args.configs.split(",") if item.strip()]
    allowed = {"dense", "bm25", "hybrid", "hybrid+scope", "hybrid+rerank"}
    unknown = set(configs) - allowed
    if unknown:
        raise SystemExit(f"未知配置: {unknown}，可选 {allowed}")

    result = asyncio.run(
        evaluate(golden, configs, top_k=args.top_k, candidate_k=args.candidate_k)
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"rag_eval_{stamp}.json"
    md_path = REPORT_DIR / f"rag_eval_{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")

    print("\n" + render_markdown(result))
    print(f"已写入：{json_path}")
    print(f"已写入：{md_path}")


if __name__ == "__main__":
    main()
