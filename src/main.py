"""
Pipeline entry point.

Runs the whole project end to end:

    generate  ->  clean  ->  load into SQLite  ->  analyse  ->  export

Each stage is also runnable on its own (python3 -m src.generate_telemetry
and so on) for development. This module exists so that someone who has just
cloned the repository can reproduce everything with one command.


"""

import argparse
import time

from src import analysis, clean_telemetry, database, export_results, generate_telemetry


def run_stage(name, function, *args, **kwargs):
    """Run one pipeline stage with timing and a heading."""
    print(f"\n{'=' * 72}")
    print(f"  {name}")
    print("=" * 72)

    start = time.perf_counter()
    result = function(*args, **kwargs)
    elapsed = time.perf_counter() - start

    print(f"\n  [{name} completed in {elapsed:.1f}s]")
    return result


def run_pipeline(skip_generate=False):
    """Run every stage of the pipeline in order."""
    total_start = time.perf_counter()

    print("FORMULA SAE TELEMETRY ANALYZER")
    print("Synthetic telemetry pipeline")

    if skip_generate:
        print("\n(Skipping generation; using existing data/raw/telemetry.csv)")
    else:
        run_stage("1. Generate synthetic telemetry", generate_telemetry.main)

    run_stage("2. Clean and aggregate", clean_telemetry.main)
    run_stage("3. Load into SQLite", database.main)
    run_stage("4. Analyse", analysis.full_report)
    run_stage("5. Export for dashboards", export_results.main)

    total = time.perf_counter() - total_start
    print(f"\n{'=' * 72}")
    print(f"  Pipeline complete in {total:.1f}s")
    print("=" * 72)
    print("\nOutputs:")
    print("  data/raw/telemetry.csv        raw simulated samples")
    print("  data/processed/               cleaned data and summaries")
    print("  data/telemetry.db             SQLite database")
    print("  data/exports/                 CSVs for Power BI / Tableau")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Run the Formula SAE telemetry pipeline."
    )
    parser.add_argument(
        "--skip-generate",
        action="store_true",
        help="reuse the existing raw telemetry instead of regenerating it",
    )
    args = parser.parse_args()

    run_pipeline(skip_generate=args.skip_generate)


if __name__ == "__main__":
    main()