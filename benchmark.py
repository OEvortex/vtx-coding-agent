#!/usr/bin/env python3
"""
Vortexa Indexer Benchmark — VTX Codebase
=========================================
Benchmarks TWO embedding models from the vortexa library on the VTX codebase:

  Model A: VTXAI/Vortex-Embed-4.7M  (LF4Embedder  — 4-bit static, ~3.5 MB)
  Model B: AI4free/JARVIS-tool-search-v1 (Model2VecEmbedder — static, ~30 MB)

Metrics reported per model
  - Indexing time (ms)
  - Peak RAM usage during index (MB)
  - Number of files indexed
  - Number of chunks produced
  - Chunk config (size / overlap)
  - Model size / embedding dim
  - Per-query latency (mean / p50 / p95 / p99)
  - R@1  — query-level: was the ground-truth file ranked #1?
  - R@5  — query-level: was the ground-truth file in top-5?
  - R@10 — query-level: was the ground-truth file in top-10?

30-query test set is hand-crafted from real VTX source symbols and concepts.

Usage:
    uv run python benchmark.py
    uv run python benchmark.py --root /path/to/other/codebase
    uv run python benchmark.py --alpha 0.7   # override hybrid weight
    uv run python benchmark.py --no-m2v      # skip Model2Vec (faster)
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── stdlib resource tracking ─────────────────────────────────────────────────
try:
    import resource as _resource

    def _peak_rss_mb() -> float:
        """Peak resident-set-size in MB (Linux/macOS)."""
        return _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss / 1024

except ImportError:  # Windows

    def _peak_rss_mb() -> float:
        try:
            import psutil

            return psutil.Process().memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0


# ── vortexa imports ───────────────────────────────────────────────────────────
try:
    from vortexa.core.embedding import LF4Embedder, Model2VecEmbedder
    from vortexa.core.indexer import CodebaseIndexer
    from vortexa.core.types import ChunkConfig, IndexStats
except ModuleNotFoundError as exc:
    sys.exit(
        f"\n[ERROR] Could not import vortexa: {exc}\n\n"
        "vortexa must be available in the SAME Python environment used to run this\n"
        "script. The simplest ways:\n\n"
        "  # Recommended — uv injects vortexa into a fresh env at runtime:\n"
        "  uv run --with vortexa python benchmark.py\n\n"
        "  # Or add it to the project venv permanently:\n"
        "  uv add --dev vortexa\n"
        "  uv run python benchmark.py\n\n"
        "Do NOT run with .venv/bin/python directly; that venv lacks vortexa.\n"
    )

# ═════════════════════════════════════════════════════════════════════════════
# 30-Query Test Suite
# Each entry: (query_text, expected_file_substring_or_None, description)
# expected_file = None  →  query is "open", we still measure latency but skip R@k
# expected_file = str   →  ground truth: result.chunk.file_path must CONTAIN this
# ═════════════════════════════════════════════════════════════════════════════
QUERIES: list[tuple[str, str | None, str]] = [
    # ── Core agent runner ──────────────────────────────────────────────────
    ("agent runner main loop execution", "agent_runner", "Agent runner entry point"),
    ("how does the agent run tasks asynchronously", "agent_runner", "Async task execution"),
    # ── CLI ───────────────────────────────────────────────────────────────
    ("command line interface argument parsing", "cli", "CLI argument parsing"),
    ("vtx CLI entry point argparse", "cli", "CLI argparse setup"),
    # ── Config ────────────────────────────────────────────────────────────
    ("configuration loading and defaults", "config", "Config loading"),
    ("user config file path resolution", "config", "Config path resolution"),
    # ── LLM / models ──────────────────────────────────────────────────────
    ("LLM API call streaming response", "llm", "LLM streaming"),
    ("model provider selection gemini openai", "llm", "Model provider"),
    ("token counting and context window management", "llm", "Token counting"),
    # ── Context / governance ──────────────────────────────────────────────
    ("context window governance truncation", "context_governance", "Context governance"),
    ("context item priority ranking", "context", "Context priority"),
    # ── Dispatcher ────────────────────────────────────────────────────────
    ("tool dispatcher routing function calls", "dispatcher", "Tool dispatcher"),
    ("dispatch tool call with arguments", "dispatcher", "Tool call dispatch"),
    # ── Events ────────────────────────────────────────────────────────────
    ("event system publish subscribe hooks", "events", "Event system"),
    ("streaming event output to terminal", "events", "Event streaming"),
    # ── Git integration ───────────────────────────────────────────────────
    ("git branch creation and switching", "git_branch", "Git branch"),
    ("github CLI integration shell command", "gh_cli", "GitHub CLI"),
    # ── Diff display ──────────────────────────────────────────────────────
    ("diff display unified patch format", "diff_display", "Diff display"),
    ("show file changes colored diff output", "diff_display", "Colored diff"),
    # ── Extensions ────────────────────────────────────────────────────────
    ("plugin extension registration loading", "extensions", "Extension loading"),
    # ── Skills / builtin ──────────────────────────────────────────────────
    ("builtin skill definition and registration", "builtin_skill", "Builtin skills"),
    ("skill slash command frontmatter parsing", "builtin_skill", "Skill frontmatter"),
    # ── Agents ────────────────────────────────────────────────────────────
    ("subagent invocation and communication", "agents", "Subagent invocation"),
    ("agent message passing protocol", "agents", "Agent messaging"),
    # ── Hooks ─────────────────────────────────────────────────────────────
    ("pre-commit hook integration", "hooks", "Pre-commit hooks"),
    # ── Async utilities ───────────────────────────────────────────────────
    ("async utility helpers gather timeout", "async_utils", "Async utilities"),
    # ── Headless mode ─────────────────────────────────────────────────────
    ("headless non-interactive execution mode", "headless", "Headless mode"),
    # ── General codebase understanding ────────────────────────────────────
    ("error handling exception retry logic", None, "Generic: error handling (open)"),
    ("logging setup structured log output", None, "Generic: logging (open)"),
    ("test fixtures mock patching pytest", "tests", "Test utilities"),
]

assert len(QUERIES) == 30, f"Expected 30 queries, got {len(QUERIES)}"


# ═════════════════════════════════════════════════════════════════════════════
# Data classes for results
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class QueryResult:
    query: str
    description: str
    expected_file: str | None
    latency_ms: float
    top1_file: str | None
    top5_files: list[str]
    top10_files: list[str]
    hit_at_1: bool | None  # None = open query (no ground truth)
    hit_at_5: bool | None
    hit_at_10: bool | None
    top1_score: float


@dataclass
class ModelBenchResult:
    model_name: str
    model_label: str
    embedding_dim: int
    model_size_mb: float
    index_dir: str

    # Index stats
    indexed_files: int = 0
    total_chunks: int = 0
    chunk_size: int = 0
    chunk_overlap: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    index_time_ms: float = 0.0
    peak_ram_mb: float = 0.0
    memo_hits: int = 0
    memo_misses: int = 0

    # Search results
    query_results: list[QueryResult] = field(default_factory=list)

    # Aggregate metrics
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mean_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    evaluated_queries: int = 0
    open_queries: int = 0


# ═════════════════════════════════════════════════════════════════════════════
# Benchmark runner
# ═════════════════════════════════════════════════════════════════════════════
def _check_hit(result_files: list[str], expected: str) -> bool:
    return any(expected.lower() in f.lower() for f in result_files)


def run_benchmark(
    root: Path,
    model_label: str,
    embedder: Any,
    alpha: float | None,
    top_k: int = 10,
    verbose: bool = True,
) -> ModelBenchResult:
    model_name = getattr(embedder, "_model_id", None) or getattr(
        embedder, "_model_name", "unknown"
    )
    if verbose:
        print(f"\n{'═' * 60}")
        print(f"  Benchmarking: {model_label}")
        print(f"  Model ID   : {model_name}")
        print(f"{'═' * 60}")

    # Temporary isolated index dir to avoid cache pollution between runs
    tmp_index = tempfile.mkdtemp(prefix=f"vtx_bench_{model_label.replace(' ', '_')}_")

    chunk_cfg = ChunkConfig(chunk_size=1500, chunk_overlap=200)
    indexer = CodebaseIndexer(
        root=root, model=embedder, index_dir=tmp_index, chunk_config=chunk_cfg
    )

    # ── Index ────────────────────────────────────────────────────────────
    gc.collect()
    ram_before = _peak_rss_mb()

    if verbose:
        print("  [1/3] Indexing codebase …", end="", flush=True)

    t0 = time.perf_counter()
    stats: IndexStats = indexer.index(force=True, include_text_files=False)
    index_elapsed = (time.perf_counter() - t0) * 1000

    ram_after = _peak_rss_mb()
    peak_ram = max(ram_after - ram_before, 0.0)

    if verbose:
        print(f" done in {index_elapsed:.0f} ms  ({stats.total_chunks} chunks)")

    # Embedding dim and model size
    emb_dim = embedder.dim
    model_size_mb = 0.0
    # Try to get the size from the underlying model
    if hasattr(embedder, "_model") and embedder._model is not None:
        raw = embedder._model
        if hasattr(raw, "model_size_mb"):
            model_size_mb = raw.model_size_mb
        elif hasattr(raw, "dim"):
            model_size_mb = 0.0  # model2vec doesn't expose size easily

    result = ModelBenchResult(
        model_name=model_name,
        model_label=model_label,
        embedding_dim=emb_dim,
        model_size_mb=model_size_mb,
        index_dir=tmp_index,
        indexed_files=stats.indexed_files,
        total_chunks=stats.total_chunks,
        chunk_size=chunk_cfg.chunk_size,
        chunk_overlap=chunk_cfg.chunk_overlap,
        languages=dict(stats.languages),
        index_time_ms=round(index_elapsed, 1),
        peak_ram_mb=round(peak_ram, 1),
        memo_hits=stats.memo_hits,
        memo_misses=stats.memo_misses,
    )

    # ── Search — 30 queries ───────────────────────────────────────────────
    if verbose:
        print(f"  [2/3] Running {len(QUERIES)} queries …")

    latencies: list[float] = []
    hits1: list[bool] = []
    hits5: list[bool] = []
    hits10: list[bool] = []
    open_count = 0

    for i, (query, expected_file, description) in enumerate(QUERIES):
        t_q = time.perf_counter()
        search_results = indexer.search(query, top_k=top_k, alpha=alpha)
        q_elapsed = (time.perf_counter() - t_q) * 1000
        latencies.append(q_elapsed)

        top_files_all = [r.chunk.file_path for r in search_results]
        top1_file = top_files_all[0] if top_files_all else None
        top5_files = top_files_all[:5]
        top10_files = top_files_all[:10]
        top1_score = search_results[0].score if search_results else 0.0

        if expected_file is None:
            hit1 = hit5 = hit10 = None
            open_count += 1
        else:
            hit1 = _check_hit(top_files_all[:1], expected_file)
            hit5 = _check_hit(top5_files, expected_file)
            hit10 = _check_hit(top10_files, expected_file)
            hits1.append(hit1)
            hits5.append(hit5)
            hits10.append(hit10)

        qr = QueryResult(
            query=query,
            description=description,
            expected_file=expected_file,
            latency_ms=round(q_elapsed, 2),
            top1_file=top1_file,
            top5_files=top5_files,
            top10_files=top10_files,
            hit_at_1=hit1,
            hit_at_5=hit5,
            hit_at_10=hit10,
            top1_score=round(top1_score, 4),
        )
        result.query_results.append(qr)

        if verbose:
            status = ("✓" if hit1 else "~" if hit5 else "✗") if expected_file else "○"
            print(
                f"    [{i + 1:02d}/{len(QUERIES)}] {status} {description[:42]:<42} "
                f"{q_elapsed:6.1f}ms  score={top1_score:.3f}"
            )

    # ── Aggregate ─────────────────────────────────────────────────────────
    import statistics

    n_eval = len(hits1)
    result.evaluated_queries = n_eval
    result.open_queries = open_count
    result.recall_at_1 = round(sum(hits1) / n_eval, 4) if n_eval else 0.0
    result.recall_at_5 = round(sum(hits5) / n_eval, 4) if n_eval else 0.0
    result.recall_at_10 = round(sum(hits10) / n_eval, 4) if n_eval else 0.0
    result.mean_latency_ms = round(statistics.mean(latencies), 2)
    result.p50_latency_ms = round(statistics.median(latencies), 2)
    sorted_lat = sorted(latencies)
    result.p95_latency_ms = round(sorted_lat[int(0.95 * len(sorted_lat)) - 1], 2)
    result.p99_latency_ms = round(sorted_lat[int(0.99 * len(sorted_lat)) - 1], 2)

    if verbose:
        print("  [3/3] Cleanup temporary index …")

    shutil.rmtree(tmp_index, ignore_errors=True)

    return result


# ═════════════════════════════════════════════════════════════════════════════
# Rich terminal report
# ═════════════════════════════════════════════════════════════════════════════
_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_MAGENTA = "\033[95m"
_DIM = "\033[2m"


def _pct(v: float) -> str:
    colour = _GREEN if v >= 0.8 else (_YELLOW if v >= 0.5 else _RED)
    return f"{colour}{v * 100:.1f}%{_RESET}"


def _ms(v: float) -> str:
    colour = _GREEN if v < 50 else (_YELLOW if v < 200 else _RED)
    return f"{colour}{v:.1f}ms{_RESET}"


def print_report(results: list[ModelBenchResult], alpha: float | None) -> None:
    print(f"\n{_BOLD}{'═' * 70}{_RESET}")
    print(f"{_BOLD}{'  VORTEXA BENCHMARK REPORT':^70}{_RESET}")
    print(f"{_BOLD}{'═' * 70}{_RESET}")
    print("  Codebase : VTX (vtx-coding-agent)")
    n_eval_q = sum(1 for q in QUERIES if q[1] is not None)
    n_open_q = sum(1 for q in QUERIES if q[1] is None)
    print(f"  Queries  : {len(QUERIES)} total  ({n_eval_q} evaluated, {n_open_q} open)")
    print(f"  alpha    : {'adaptive (auto)' if alpha is None else alpha}")
    print()

    # ── Summary table ─────────────────────────────────────────────────────
    col = 26
    hdr = f"{'Metric':<{col}}"
    for r in results:
        hdr += f"  {_BOLD}{r.model_label[:22]:<22}{_RESET}"
    print(hdr)
    print(f"{'─' * (col + len(results) * 24)}")

    def row(label: str, vals: list[str]) -> None:
        line = f"{label:<{col}}"
        for v in vals:
            line += f"  {v:<22}"
        print(line)

    # Index metrics
    row("Model ID", [f"{_DIM}{r.model_name[:20]}{_RESET}" for r in results])
    row("Embedding dim", [str(r.embedding_dim) for r in results])
    row(
        "Model size (MB)",
        [f"{r.model_size_mb:.1f}" if r.model_size_mb else "n/a" for r in results],
    )
    row("Chunk size / overlap", [f"{r.chunk_size} / {r.chunk_overlap}" for r in results])
    row("Files indexed", [str(r.indexed_files) for r in results])
    row("Total chunks", [str(r.total_chunks) for r in results])
    row("Memo hits / misses", [f"{r.memo_hits} / {r.memo_misses}" for r in results])
    row("Index time", [_ms(r.index_time_ms) for r in results])
    row("Peak RAM delta (MB)", [f"{r.peak_ram_mb:.1f}" for r in results])
    print(f"{'─' * (col + len(results) * 24)}")

    # Retrieval metrics
    row("R@1  (evaluated)", [_pct(r.recall_at_1) for r in results])
    row("R@5  (evaluated)", [_pct(r.recall_at_5) for r in results])
    row("R@10 (evaluated)", [_pct(r.recall_at_10) for r in results])
    print(f"{'─' * (col + len(results) * 24)}")

    # Latency
    row("Latency mean", [_ms(r.mean_latency_ms) for r in results])
    row("Latency p50", [_ms(r.p50_latency_ms) for r in results])
    row("Latency p95", [_ms(r.p95_latency_ms) for r in results])
    row("Latency p99", [_ms(r.p99_latency_ms) for r in results])
    print(f"{'─' * (col + len(results) * 24)}")

    # Languages
    for r in results:
        langs_str = ", ".join(
            f"{k}:{v}" for k, v in sorted(r.languages.items(), key=lambda x: -x[1])[:5]
        )
        row("Top languages", [langs_str if rr is r else "" for rr in results])
        break  # Only one row for language overview

    print()

    # ── Per-query breakdown ───────────────────────────────────────────────
    print(f"{_BOLD}Per-Query Breakdown{_RESET}")
    print(f"{'─' * 100}")
    header = f"{'#':<3} {'Description':<44} {'Expected':<18}"
    for _r in results:
        header += f"  {'@1 @5 @10':^12}  {'ms':>6}"
    print(header)
    print(f"{'─' * 100}")

    for i, (_query, expected, desc) in enumerate(QUERIES):
        line = f"{i + 1:<3} {desc[:43]:<44} {(expected or '(open)')[:17]:<18}"
        for r in results:
            qr = r.query_results[i]
            if qr.hit_at_1 is None:
                hits = f"{'○':^4} {'○':^4} {'○':^4}"
            else:
                h1 = f"{_GREEN}✓{_RESET}" if qr.hit_at_1 else f"{_RED}✗{_RESET}"
                h5 = f"{_GREEN}✓{_RESET}" if qr.hit_at_5 else f"{_RED}✗{_RESET}"
                h10 = f"{_GREEN}✓{_RESET}" if qr.hit_at_10 else f"{_RED}✗{_RESET}"
                hits = f"{h1:<4} {h5:<4} {h10:<4}"
            line += f"  {hits}  {qr.latency_ms:6.1f}"
        print(line)

    print(f"{'─' * 100}")
    print()


# ═════════════════════════════════════════════════════════════════════════════
# Save JSON report
# ═════════════════════════════════════════════════════════════════════════════
def save_json_report(results: list[ModelBenchResult], output_path: Path) -> None:
    report = []
    for r in results:
        d = asdict(r)
        report.append(d)
    output_path.write_text(json.dumps(report, indent=2))
    print(f"  JSON report saved → {output_path}")


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark vortexa indexers on the VTX codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).parent,
        help="Codebase root to index (default: this file's directory)",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=None,
        help="Hybrid search alpha (0=BM25, 1=semantic, None=adaptive)",
    )
    parser.add_argument(
        "--top-k", type=int, default=10, help="Max results per query (default: 10)"
    )
    parser.add_argument(
        "--no-m2v", action="store_true", help="Skip Model2Vec benchmark (faster, fewer deps)"
    )
    parser.add_argument(
        "--no-lf4", action="store_true", help="Skip LF4 (Vortex-Embed-4.7M) benchmark"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark_results.json"),
        help="Path for JSON report output",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-query progress output")
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.exists():
        sys.exit(f"[ERROR] Root not found: {root}")

    verbose = not args.quiet

    print(f"\n{_BOLD}{_CYAN}Vortexa Indexer Benchmark{_RESET}")
    print(f"{_DIM}Codebase root: {root}{_RESET}")
    import importlib.util

    _vsite = importlib.util.find_spec("vortexa")
    _vloc = Path(_vsite.origin).parent if _vsite else "unknown"
    print(f"{_DIM}vortexa site : {_vloc}{_RESET}\n")

    models_to_run: list[tuple[str, Any]] = []

    if not args.no_lf4:
        models_to_run.append(("LF4 Vortex-Embed-4.7M", LF4Embedder("VTXAI/Vortex-Embed-4.7M")))

    if not args.no_m2v:
        models_to_run.append(
            ("Model2Vec JARVIS-tool-search", Model2VecEmbedder("AI4free/JARVIS-tool-search-v1"))
        )

    if not models_to_run:
        sys.exit("[ERROR] All models disabled — enable at least one.")

    # Pre-warm: touch both models to download weights before we start timing
    print(f"{_BOLD}Pre-warming models (downloading weights if needed)…{_RESET}")
    for label, emb in models_to_run:
        print(f"  loading {label} …", end="", flush=True)
        t_load = time.perf_counter()
        _ = emb.dim  # triggers lazy load
        print(f" ready in {(time.perf_counter() - t_load) * 1000:.0f}ms")

    results: list[ModelBenchResult] = []

    for label, emb in models_to_run:
        r = run_benchmark(
            root=root,
            model_label=label,
            embedder=emb,
            alpha=args.alpha,
            top_k=args.top_k,
            verbose=verbose,
        )
        results.append(r)

    print_report(results, args.alpha)
    save_json_report(results, args.output)

    # ── Winner callout ────────────────────────────────────────────────────
    if len(results) > 1:
        best_r1 = max(results, key=lambda r: r.recall_at_1)
        best_speed = min(results, key=lambda r: r.mean_latency_ms)
        r1_pct = f"{best_r1.recall_at_1 * 100:.1f}%"
        spd_ms = f"{best_speed.mean_latency_ms:.1f}ms mean"
        print(f"{_BOLD}Winner R@1    :{_RESET} {_GREEN}{best_r1.model_label}{_RESET}  ({r1_pct})")
        print(
            f"{_BOLD}Winner Speed  :{_RESET} {_GREEN}{best_speed.model_label}{_RESET}  ({spd_ms})"
        )
        print()


if __name__ == "__main__":
    main()
