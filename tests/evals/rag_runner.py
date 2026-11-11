"""Measure retrieval quality against a small human-labelled source set.

Run inside the API image (which can reach Qdrant):
    uv run python -m tests.evals.rag_runner --tenant demo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_agent_template.agent.graph import build_agent_graph
from ai_agent_template.agent.models import build_chat_model
from ai_agent_template.agent.service import AgentService
from ai_agent_template.api.dependencies import get_rag_service
from ai_agent_template.api.schemas import AgentInvokeRequest
from ai_agent_template.core.settings import get_settings
from ai_agent_template.rag.service import RagService
from ai_agent_template.rag.vectorstore import build_vector_store

CASES = Path(__file__).with_name("rag_golden.jsonl")


@dataclass(frozen=True)
class RagEvalCase:
    id: str
    query: str
    relevant_sources: tuple[str, ...]


def load_cases(path: Path = CASES) -> list[RagEvalCase]:
    return [
        RagEvalCase(
            id=row["id"],
            query=row["query"],
            relevant_sources=tuple(row["relevant_sources"]),
        )
        for line in path.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line.strip()))
    ]


def retrieval_scores(relevant: set[str], retrieved: list[str]) -> tuple[float, float, float]:
    """Return Recall@K, reciprocal rank, and Precision@K for one query."""
    found = [source for source in retrieved if source in relevant]
    recall = len(set(found)) / len(relevant) if relevant else 0.0
    precision = len(found) / len(retrieved) if retrieved else 0.0
    first_rank = next(
        (rank for rank, source in enumerate(retrieved, start=1) if source in relevant), None
    )
    reciprocal_rank = 1 / first_rank if first_rank else 0.0
    return recall, reciprocal_rank, precision


def citation_accuracy(answer: str, retrieved_sources: set[str]) -> float | None:
    """Mechanical citation-label precision; does not judge whether a source entails a claim."""
    citations = re.findall(r"\[([^\[\]]+)\]", answer)
    if not citations:
        return None
    return (
        sum(citation in retrieved_sources for citation in citations) / len(citations)
        if citations
        else 0.0
    )


async def score_generated_citations(
    agent: AgentService,
    cases: list[RagEvalCase],
    tenant_id: str,
    relevant_retrieved_by_case: dict[str, set[str]],
) -> list[tuple[str, float, str]]:
    scores: list[tuple[str, float, str]] = []
    for case in cases:
        if not case.relevant_sources:
            continue
        answer = await agent.invoke(AgentInvokeRequest(message=case.query), tenant_id=tenant_id)
        score = citation_accuracy(answer.message, relevant_retrieved_by_case[case.id])
        scores.append((case.id, score or 0.0, answer.message))
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tenant", default="demo", help="Tenant ID whose knowledge base to evaluate"
    )
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=None,
        help="Try a relevance threshold without changing app configuration",
    )
    parser.add_argument(
        "--with-answers",
        action="store_true",
        help="Call the configured chat model and score cited source labels",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    top_k = args.top_k or settings.rag_top_k
    rag = get_rag_service()
    if args.score_threshold is not None:
        eval_settings = settings.model_copy(update={"rag_score_threshold": args.score_threshold})
        rag = RagService(settings=eval_settings, vector_store=build_vector_store(eval_settings))
    agent = None
    if args.with_answers:
        agent = AgentService(build_agent_graph(build_chat_model(settings), retriever=rag))
    cases = load_cases()
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    precisions: list[float] = []
    citation_scores: list[float] = []
    abstention_results: list[bool] = []
    relevant_retrieved_by_case: dict[str, set[str]] = {}
    for case in cases:
        sources = rag.search(case.query, tenant_id=args.tenant)[:top_k]
        labels = [source.source for source in sources]
        if not case.relevant_sources:
            abstained = len(labels) == 0
            abstention_results.append(abstained)
            print(
                f"[{case.id}] expected_abstention="
                f"{'PASS' if abstained else 'FAIL'} retrieved={labels}"
            )
            continue
        recall, reciprocal_rank, precision = retrieval_scores(set(case.relevant_sources), labels)
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        precisions.append(precision)
        relevant_retrieved_by_case[case.id] = set(labels).intersection(case.relevant_sources)
        print(
            f"[{case.id}] Recall@{top_k}={recall:.2f} "
            f"RR={reciprocal_rank:.2f} Precision@{top_k}={precision:.2f} "
            f"retrieved={[(source.source, round(source.score or 0, 3)) for source in sources]}"
        )

    count = len(recalls) or 1
    print(
        f"\n{len(recalls)} retrieval cases | Recall@{top_k}={sum(recalls) / count:.3f} "
        f"MRR={sum(reciprocal_ranks) / count:.3f} "
        f"Precision@{top_k}={sum(precisions) / count:.3f}"
    )
    if abstention_results:
        print(f"Abstention accuracy={sum(abstention_results) / len(abstention_results):.3f}")
    if args.with_answers:
        assert agent is not None
        generated = asyncio.run(
            score_generated_citations(agent, cases, args.tenant, relevant_retrieved_by_case)
        )
        for case_id, score, answer in generated:
            citation_scores.append(score)
            print(f"[{case_id}] citation-source-accuracy={score:.2f} cited-answer={answer[:240]!r}")
        print(f"Citation source accuracy={sum(citation_scores) / max(len(citation_scores), 1):.3f}")
    retrieval_ok = all(score == 1.0 for score in recalls)
    abstention_ok = all(abstention_results)
    return 0 if retrieval_ok and abstention_ok else 1


if __name__ == "__main__":
    sys.exit(main())
