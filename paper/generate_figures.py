"""
paper/generate_figures.py
=========================
Renders high-resolution vector and raster academic figures using Matplotlib.
Generates:
  - fig1_throughput.png: Row Hydration Throughput (Model vs values())
  - fig2_memory.png: Memory Allocation Profile (KB)
  - fig3_nplusone.png: Eager (select_related) vs Lazy Loading Latency
  - fig4_compilation.png: AST Compilation Latency (microseconds)
"""

from __future__ import annotations

import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def generate_all_figures() -> None:
    os.makedirs("paper/figures", exist_ok=True)

    with open("paper/benchmark_results.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # Style configuration for IEEE/ACM academic papers
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 10,
        "axes.labelsize": 10,
        "axes.titlesize": 11,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "figure.titlesize": 12,
        "figure.dpi": 300,
    })

    # --- Figure 1: Row Hydration Throughput ---
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    tiers = ["1,000", "5,000", "10,000"]
    x = [1, 2, 3]
    width = 0.35

    model_tp = [
        data["hydration"]["1000"]["model_throughput_rows_sec"] / 1000,
        data["hydration"]["5000"]["model_throughput_rows_sec"] / 1000,
        data["hydration"]["10000"]["model_throughput_rows_sec"] / 1000,
    ]
    values_tp = [
        data["hydration"]["1000"]["values_throughput_rows_sec"] / 1000,
        data["hydration"]["5000"]["values_throughput_rows_sec"] / 1000,
        data["hydration"]["10000"]["values_throughput_rows_sec"] / 1000,
    ]

    ax.bar([i - width/2 for i in x], model_tp, width, label="Model Hydration", color="#2b5c8f")
    ax.bar([i + width/2 for i in x], values_tp, width, label="values() Projection", color="#4ba3e3")

    ax.set_ylabel("Throughput (k-rows / sec)")
    ax.set_xlabel("Dataset Volume (Entities)")
    ax.set_title("Row Hydration Throughput Comparison")
    ax.set_xticks(x)
    ax.set_xticklabels(tiers)
    ax.legend(frameon=True, loc="upper left")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig("paper/figures/fig1_throughput.png")
    plt.close()

    # --- Figure 2: Memory Footprint Profile ---
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    model_mem = [
        data["memory"]["1000"]["model_memory_kb"],
        data["memory"]["5000"]["model_memory_kb"],
        data["memory"]["10000"]["model_memory_kb"],
    ]
    values_mem = [
        data["memory"]["1000"]["values_memory_kb"],
        data["memory"]["5000"]["values_memory_kb"],
        data["memory"]["10000"]["values_memory_kb"],
    ]

    ax.plot(x, model_mem, marker="o", linewidth=2, label="Model Instances", color="#d95f02")
    ax.plot(x, values_mem, marker="s", linewidth=2, linestyle="--", label="values() Tuples/Dicts", color="#7570b3")

    ax.set_ylabel("Peak Allocated Memory (KB)")
    ax.set_xlabel("Entity Count")
    ax.set_title("Heap Allocation Scaling (tracemalloc)")
    ax.set_xticks(x)
    ax.set_xticklabels(tiers)
    ax.legend(frameon=True)
    ax.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig("paper/figures/fig2_memory.png")
    plt.close()

    # --- Figure 3: Eager Loading (select_related) vs N+1 ---
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    categories = ["Eager (select_related)", "Lazy (N+1 Query)"]
    latencies = [data["nplusone"]["eager_time_ms"], data["nplusone"]["lazy_time_ms"]]
    colors = ["#1b9e77", "#d95f02"]

    bars = ax.bar(categories, latencies, color=colors, width=0.5)
    for bar in bars:
        yval = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2.0, yval + 0.8, f"{yval:.1f} ms", ha="center", va="bottom", fontsize=8)

    ax.set_ylabel("Total Fetch Latency (ms)")
    ax.set_title("N=500 Related Entities Resolution Time")
    ax.set_ylim(0, max(latencies) * 1.2)
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig("paper/figures/fig3_nplusone.png")
    plt.close()

    # --- Figure 4: Compilation Latency ---
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    query_types = ["Simple\nFilter", "Joined\n(select_related)", "Composite\nQ AST"]
    comp_us = [
        data["compilation"]["simple_filter_us"],
        data["compilation"]["select_related_us"],
        data["compilation"]["complex_q_filter_us"],
    ]
    ax.bar(query_types, comp_us, color="#386cb0", width=0.45)
    for i, v in enumerate(comp_us):
        ax.text(i, v + 4, f"{v:.1f} $\\mu$s", ha="center", va="bottom", fontsize=8)

    ax.set_ylabel("Compilation Latency ($\\mu$s / query)")
    ax.set_title("AST-to-SQL Lowering Overhead")
    ax.set_ylim(0, max(comp_us) * 1.25)
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig("paper/figures/fig4_compilation.png")
    plt.close()

    print("All 4 academic figures generated in paper/figures/")


if __name__ == "__main__":
    generate_all_figures()
