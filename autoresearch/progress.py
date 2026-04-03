"""
Render a simple progress chart from results.tsv.

Usage:
    uv run progress.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parent
TSV_PATH = ROOT / "results.tsv"
OUT_PATH = ROOT / "progress.png"


def main() -> int:
    if not TSV_PATH.exists():
        print("results.tsv not found.")
        return 0

    df = pd.read_csv(TSV_PATH, sep="\t")
    if df.empty:
        print("results.tsv is empty.")
        return 0

    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df["min_free_gb"] = pd.to_numeric(df["min_free_gb"], errors="coerce")
    df["status"] = df["status"].fillna("").str.lower()
    df["backend"] = df["backend"].fillna("")
    df["run_index"] = range(len(df))

    valid = df["status"].isin(["keep", "discard"])
    df["running_best"] = df["score"].where(valid).cummax()

    fig, ax1 = plt.subplots(figsize=(14, 8))
    ax2 = ax1.twinx()

    palette = {
        "llama.cpp": "#1f77b4",
        "hypura": "#d62728",
        "flashmoe": "#2ca02c",
        "flashmoe_server": "#17a589",
    }
    status_alpha = {
        "keep": 1.0,
        "discard": 0.5,
        "reject": 0.4,
        "crash": 0.25,
    }

    for backend, group in df.groupby("backend"):
        color = palette.get(backend, "#6c757d")
        ax1.scatter(
            group["run_index"],
            group["score"],
            label=backend,
            color=color,
            alpha=[status_alpha.get(status, 0.5) for status in group["status"]],
            s=80,
            edgecolor="black",
            linewidth=0.4,
        )
        ax2.plot(
            group["run_index"],
            group["min_free_gb"],
            color=color,
            linewidth=1.2,
            alpha=0.35,
        )

    ax1.step(
        df["run_index"],
        df["running_best"],
        where="post",
        color="#111111",
        linewidth=2.2,
        label="running best",
    )

    ax1.set_xlabel("Run")
    ax1.set_ylabel("Generation tok/s")
    ax2.set_ylabel("Minimum free memory (GB)")
    ax1.set_title("Gemma 4 Mac Runtime Research Loop")
    ax1.grid(alpha=0.2)
    ax1.legend(loc="upper left")

    plt.tight_layout()
    plt.savefig(OUT_PATH, dpi=150, bbox_inches="tight")
    print(f"Saved {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
