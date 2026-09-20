"""
Runner for the saved SQL queries.

Queries live in sql/*.sql, each preceded by a "-- name: <id>" comment.
This module splits those files on the name markers so any query can be run
by name from the command line or imported into the notebook.


"""

import re
import sys

from src.database import run_query

SQL_FILES = [
    "sql/lap_analysis.sql",
    "sql/vehicle_health.sql",
]

# Matches "-- name: some_identifier" at the start of a line
NAME_PATTERN = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)


def load_queries(paths=None):
    """Return a dict of query name -> SQL text, read from the .sql files."""
    paths = paths or SQL_FILES
    queries = {}

    for path in paths:
        with open(path, "r") as f:
            content = f.read()

        matches = list(NAME_PATTERN.finditer(content))
        for index, match in enumerate(matches):
            name = match.group(1)
            start = match.end()
            # A query runs until the next name marker, or the end of file
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            queries[name] = content[start:end].strip()

    return queries


def run_named_query(name, paths=None):
    """Run one saved query by name and return the result as a DataFrame."""
    queries = load_queries(paths)
    if name not in queries:
        available = ", ".join(sorted(queries))
        raise KeyError(f"No query named '{name}'. Available: {available}")
    return run_query(queries[name])


def main():
    queries = load_queries()

    if len(sys.argv) < 2:
        print("Saved queries:\n")
        for name in sorted(queries):
            # Show the first comment line under the name marker as a hint
            first_comment = ""
            for line in queries[name].splitlines():
                if line.strip().startswith("--"):
                    first_comment = line.strip().lstrip("- ").strip()
                    break
            print(f"  {name:<32} {first_comment}")
        print(f"\nRun one with:  python3 -m src.run_sql <name>")
        return

    name = sys.argv[1]
    result = run_named_query(name)
    print(f"\n{name}  ({len(result)} rows)\n")
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()