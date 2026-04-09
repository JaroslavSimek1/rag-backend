#!/usr/bin/env python3
"""
Mini-benchmark: 25 control queries with expected keywords in answers.
Tests RAG quality by checking if answers contain expected information.

Usage:
    python benchmark.py [--url http://localhost:8000]
"""

import sys
import time
import requests
import argparse

# Control queries: (query, expected_keywords_in_answer)
# These should be adapted to the actual ingested content.
BENCHMARK_QUERIES = [
    ("Co je MENDELU?", ["mendelu", "univerzita", "brno"]),
    ("Jaké fakulty má Mendelova univerzita?", ["fakulta", "agronomická"]),
    ("Kde se nachází MENDELU?", ["brno"]),
    ("Co je Masarykova univerzita?", ["masarykova", "univerzita"]),
    ("Jaké studijní programy nabízí MENDELU?", ["studijní", "program"]),
    ("Kde najdu informace pro uchazeče?", ["uchazeč"]),
    ("Co je to RAG systém?", []),  # May not have answer — tests graceful fallback
    ("Jaké jsou fakulty MU?", ["fakulta"]),
    ("Co je arboretum Křtiny?", ["křtiny"]),
    ("Jak se přihlásit ke studiu?", ["přihlás"]),
    ("Kde najdu kontakty na univerzitu?", ["kontakt"]),
    ("Jaké jsou termíny přijímacích zkoušek?", ["termín"]),
    ("Co nabízí zahradnická fakulta?", ["zahradnick"]),
    ("Kde je lesnicka fakulta?", ["lesnick"]),
    ("Jaké jsou možnosti doktorského studia?", ["doktorsk"]),
    ("Co je provozně ekonomická fakulta?", ["ekonomick"]),
    ("Jaké služby nabízí poradenské centrum?", ["poraden"]),
    ("Co je institut celoživotního vzdělávání?", ["celoživotn"]),
    ("Kde najdu informace o vědě a výzkumu?", ["výzkum"]),
    ("Jaké jsou možnosti pro zaměstnance?", ["zaměstnanc"]),
    ("Co je Qdrant?", []),  # Out of domain test
    ("Jaký je rozdíl mezi bakalářským a magisterským studiem?", ["bakalář"]),
    ("Kde najdu mapu stránek?", ["mapa"]),
    ("Co jsou aktuality na MENDELU?", ["aktualit"]),
    ("Jak funguje přijímací řízení?", ["přijímac"]),
]


def run_benchmark(base_url: str):
    print(f"\n{'='*60}")
    print(f"  RAG Mini-Benchmark — {len(BENCHMARK_QUERIES)} queries")
    print(f"  Target: {base_url}")
    print(f"{'='*60}\n")

    results = []
    total_time = 0

    for i, (query, expected_keywords) in enumerate(BENCHMARK_QUERIES, 1):
        start = time.time()
        try:
            resp = requests.post(
                f"{base_url}/api/search",
                json={"query": query, "k": 5},
                timeout=30,
            )
            elapsed = time.time() - start
            total_time += elapsed

            if resp.status_code != 200:
                results.append({"query": query, "pass": False, "reason": f"HTTP {resp.status_code}", "time": elapsed})
                print(f"  [{i:02d}] FAIL  ({elapsed:.1f}s) — HTTP {resp.status_code}: {query[:50]}")
                continue

            data = resp.json()
            answer = data.get("answer", "").lower()
            sources_count = len(data.get("sources", []))

            if not expected_keywords:
                # No expected keywords — just check we got a response
                passed = len(answer) > 10
                reason = "got response" if passed else "empty answer"
            else:
                matched = [kw for kw in expected_keywords if kw.lower() in answer]
                passed = len(matched) > 0
                reason = f"matched: {matched}" if passed else f"expected: {expected_keywords}"

            results.append({"query": query, "pass": passed, "reason": reason, "time": elapsed, "sources": sources_count})

            status = "PASS" if passed else "FAIL"
            print(f"  [{i:02d}] {status}  ({elapsed:.1f}s, {sources_count} src) — {query[:50]}")

        except Exception as e:
            elapsed = time.time() - start
            total_time += elapsed
            results.append({"query": query, "pass": False, "reason": str(e), "time": elapsed})
            print(f"  [{i:02d}] ERROR ({elapsed:.1f}s) — {str(e)[:50]}: {query[:40]}")

    # Summary
    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed
    avg_time = total_time / len(results) if results else 0

    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{len(results)} passed ({passed/len(results)*100:.0f}%)")
    print(f"  Failed:  {failed}")
    print(f"  Avg response time: {avg_time:.2f}s")
    print(f"  Total time: {total_time:.1f}s")
    print(f"{'='*60}\n")

    # Check NFR-1.1: response time < 15s
    slow = [r for r in results if r["time"] > 15]
    if slow:
        print(f"  WARNING: {len(slow)} queries exceeded 15s response time (NFR-1.1)")
    else:
        print(f"  NFR-1.1: All queries responded within 15s")

    return passed, len(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG Mini-Benchmark")
    parser.add_argument("--url", default="http://localhost:8000", help="Backend API URL")
    args = parser.parse_args()

    passed, total = run_benchmark(args.url)
    sys.exit(0 if passed > total * 0.5 else 1)
