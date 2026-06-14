#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Skill graph — DAG construction and topological validation.

Each skill module has a Signature (see signature.py) declaring inputs/outputs.
The skill graph is the directed acyclic graph formed by composing modules
via their signatures. This module validates that the graph is well-formed.

v1.1: created as part of v1.1 distillation
"""

import json
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from skills.signature import BUILTIN_SIGNATURES, Signature


def build_pipeline_graph() -> dict:
    """Build the default skill graph from built-in signatures.

    The graph shows how modules compose:
      scanner → rules → fixer → integrator
      scanner → deliberator → proposer → challenger → integrator
      integrator → reflector → anchor
    """
    nodes = {}
    edges = []

    for name, sig in BUILTIN_SIGNATURES.items():
        nodes[name] = sig.to_dict()

    # Define compositions based on signature compatibility
    compositions = [
        ("scanner", "fixer"),
        ("scanner", "deliberator"),
        ("fixer", "integrator"),
        ("deliberator", "proposer"),
        ("proposer", "challenger"),
        ("challenger", "integrator"),
        ("integrator", "reflector"),
        ("reflector", "anchor"),
    ]

    for src, dst in compositions:
        src_sig = BUILTIN_SIGNATURES.get(src)
        dst_sig = BUILTIN_SIGNATURES.get(dst)
        if src_sig and dst_sig:
            ok, missing, _ = src_sig.check_compatible(dst_sig)
            edges.append({
                "from": src,
                "to": dst,
                "compatible": ok,
                "missing_inputs": missing
            })

    # Detect cycles (should be none in a valid pipeline)
    has_cycles = _detect_cycles(nodes, edges)

    return {
        "nodes": list(nodes.keys()),
        "edges": edges,
        "edge_count": len(edges),
        "has_cycles": has_cycles,
        "valid": not has_cycles
    }


def _detect_cycles(nodes: dict, edges: list) -> bool:
    """Simple DFS cycle detection."""
    adj = {n: [] for n in nodes}
    for e in edges:
        adj[e["from"]].append(e["to"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}

    def dfs(node):
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                return True
            if color[neighbor] == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    for node in nodes:
        if color[node] == WHITE:
            if dfs(node):
                return True
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Skill graph DAG validator")
    parser.add_argument("--build", action="store_true", help="Build and display the pipeline graph")
    parser.add_argument("--validate", type=str, help="Validate a specific skill graph")

    args = parser.parse_args()

    if args.build:
        graph = build_pipeline_graph()
        print(f"  节点: {len(graph['nodes'])}")
        print(f"  边: {graph['edge_count']}")
        print(f"  环: {'[!!] 检测到环' if graph['has_cycles'] else '[OK] 无环'}")
        for e in graph["edges"]:
            icon = "[OK]" if e["compatible"] else "[!!]"
            missing = f" (缺少: {e['missing_inputs']})" if e["missing_inputs"] else ""
            print(f"  {icon} {e['from']} → {e['to']}{missing}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
