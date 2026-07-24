"""Integration test for ChromaMemoryService.retrieve_memory.

Queries a real ChromaDB memory database interactively.

Usage:
    uv run python tests/test_chroma_memory_service.py
    uv run python tests/test_chroma_memory_service.py --db-path ./memory_db
    uv run python tests/test_chroma_memory_service.py --db-path ./memory_db --llm openai
    uv run python tests/test_chroma_memory_service.py --db-path ./memory_db --llm claude
    uv run python tests/test_chroma_memory_service.py --db-path ./memory_db --llm google
"""

import argparse
import os
from datetime import datetime, timedelta

from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.memory.chroma_service import ChromaMemoryService

LLM_PROVIDER_MAP = {
    "openai": "openai",
    "claude": "claude",
    "google": "google",
    "deepinfra": "deepinfra",
    "together": "together",
    "github_copilot": "github_copilot",
}


def create_llm_service(provider: str):
    """Create an LLM service instance for the given provider."""
    service_name = LLM_PROVIDER_MAP.get(provider)
    if not service_name:
        raise ValueError(
            f"Unknown LLM provider: {provider}. "
            f"Supported: {', '.join(LLM_PROVIDER_MAP.keys())}"
        )
    manager = ServiceManager.get_instance()
    return manager.initialize_standalone_service(service_name)


def main():
    parser = argparse.ArgumentParser(
        description="Test ChromaMemoryService.retrieve_memory against a real ChromaDB"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the ChromaDB memory_db directory. "
        "Defaults to ./memory_db. Set MEMORYDB_PATH env var as alternative.",
    )
    parser.add_argument(
        "--llm",
        default=None,
        choices=list(LLM_PROVIDER_MAP.keys()),
        help="LLM provider for consolidation (openai, claude, google, etc.). "
        "Defaults to None (no LLM). Set TEST_LLM_PROVIDER env var as alternative.",
    )

    args = parser.parse_args()

    db_path = args.db_path or os.getenv("MEMORYDB_PATH") or "./memory_db"
    llm_provider = args.llm or os.getenv("TEST_LLM_PROVIDER")

    print("ChromaMemoryService retrieve_memory test")
    print(f"  DB path: {db_path}")
    print(f"  LLM provider: {llm_provider or 'None'}")

    llm_service = None
    if llm_provider:
        llm_service = create_llm_service(llm_provider)
        print(f"  LLM model: {llm_service.model}")

    os.environ["MEMORYDB_PATH"] = db_path

    service = ChromaMemoryService(
        collection_name="conversation",
        llm_service=llm_service,
    )
    service.ensure_initialized()

    try:
        print(f"\nConnected to ChromaDB at: {db_path}")
        print("Type your query to retrieve memories. Commands:")
        print("  :agent <name>   - Set agent name filter (empty = all agents)")
        print("  :from <days>    - Set from_date to N days ago")
        print("  :to <days>      - Set to_date to N days ago")
        print("  :dates          - Show current date filters")
        print("  :reset          - Reset all filters")
        print("  :quit           - Exit")
        print()

        agent_name = ""
        from_date = None
        to_date = None

        while True:
            try:
                query = input("Query> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not query:
                continue

            if query == ":quit":
                break

            if query.startswith(":agent"):
                parts = query.split(maxsplit=1)
                agent_name = parts[1] if len(parts) > 1 else ""
                print(f"  Agent filter set to: '{agent_name or '(all)'}'")
                continue

            if query.startswith(":from"):
                parts = query.split(maxsplit=1)
                if len(parts) > 1 and parts[1].isdigit():
                    days = int(parts[1])
                    from_date = int((datetime.now() - timedelta(days=days)).timestamp())
                    print(
                        f"  From date set to: {datetime.fromtimestamp(from_date).strftime('%Y-%m-%d %H:%M')}"
                    )
                else:
                    from_date = None
                    print("  From date cleared.")
                continue

            if query.startswith(":to"):
                parts = query.split(maxsplit=1)
                if len(parts) > 1 and parts[1].isdigit():
                    days = int(parts[1])
                    to_date = int((datetime.now() - timedelta(days=days)).timestamp())
                    print(
                        f"  To date set to: {datetime.fromtimestamp(to_date).strftime('%Y-%m-%d %H:%M')}"
                    )
                else:
                    to_date = None
                    print("  To date cleared.")
                continue

            if query == ":dates":
                print(
                    f"  From: {datetime.fromtimestamp(from_date).strftime('%Y-%m-%d %H:%M') if from_date else '(none)'}"
                )
                print(
                    f"  To: {datetime.fromtimestamp(to_date).strftime('%Y-%m-%d %H:%M') if to_date else '(none)'}"
                )
                continue

            if query == ":reset":
                agent_name = ""
                from_date = None
                to_date = None
                print("  All filters reset.")
                continue

            print(f"\nRetrieving memories for: '{query}'")
            print(f"  Agent: {agent_name or '(all)'}")
            if from_date:
                print(
                    f"  From: {datetime.fromtimestamp(from_date).strftime('%Y-%m-%d %H:%M')}"
                )
            if to_date:
                print(
                    f"  To: {datetime.fromtimestamp(to_date).strftime('%Y-%m-%d %H:%M')}"
                )

            result = service.retrieve_memory(
                keywords=query,
                from_date=from_date,
                to_date=to_date,
                agent_name=agent_name,
            )

            if result == "No relevant memories found.":
                print("  No relevant memories found.")
            else:
                print(result)
            print()

    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
