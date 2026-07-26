import argparse

from tabulate import tabulate

from database.db_manager import DatabaseManager
from journal_engine.research_engine import ResearchEngine


def main():
    parser = argparse.ArgumentParser(description="TraderOS Research CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Observation
    obs_parser = subparsers.add_parser("obs", help="Create an observation")
    obs_parser.add_argument("symbol", help="Market symbol")
    obs_parser.add_argument("content", help="Observation details")
    obs_parser.add_argument("--tags", default="", help="Comma-separated tags")

    # Hypothesis
    hyp_parser = subparsers.add_parser("hyp", help="Create a hypothesis from an observation")
    hyp_parser.add_argument("obs_id", type=int, help="Observation ID")
    hyp_parser.add_argument("content", help="Hypothesis details")

    # List
    subparsers.add_parser("list", help="List recent observations")

    # Trace
    trace_parser = subparsers.add_parser("trace", help="Trace a research workflow")
    trace_parser.add_argument("lesson_id", type=int, help="Lesson ID")

    args = parser.parse_args()
    db = DatabaseManager()
    engine = ResearchEngine(db)

    if args.command == "obs":
        oid = engine.create_observation(args.symbol, args.content, args.tags)
        print(f"Observation created with ID: {oid}")

    elif args.command == "hyp":
        hid = engine.create_hypothesis(args.obs_id, args.content)
        print(f"Hypothesis created with ID: {hid}")

    elif args.command == "list":
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT id, timestamp, symbol, content FROM observations ORDER BY id DESC LIMIT 10"
        )
        rows = cursor.fetchall()
        print(tabulate(rows, headers=["ID", "Timestamp", "Symbol", "Content"]))

    elif args.command == "trace":
        workflow = engine.get_full_workflow(args.lesson_id)
        if workflow:
            print("\n=== RESEARCH WORKFLOW TRACE ===")
            print(f"OBSERVATION: {workflow['observation']}")
            print(f"HYPOTHESIS:  {workflow['hypothesis']}")
            print(f"TEST:        {workflow['test']}")
            print(f"RESULT:      {workflow['result']}")
            print(f"LESSON:      {workflow['lesson']}")
        else:
            print("No workflow found for that lesson ID.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
