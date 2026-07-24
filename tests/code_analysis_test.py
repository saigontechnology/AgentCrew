import asyncio
import os

import requests

from AgentCrew.modules.code_analysis import CodeAnalysisService
from AgentCrew.modules.llm.service_manager import ServiceManager


def count_tokens(content: str, model: str = "claude-opus-4-5-20251101") -> dict:
    url = "https://api.anthropic.com/v1/messages/count_tokens"
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        "X-Api-Key": os.environ.get("ANTHROPIC_API_KEY"),
    }
    payload = {
        "messages": [{"content": content, "role": "user"}],
        "model": model,
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


async def main():
    import json

    llm_manager = ServiceManager.get_instance()
    code_analysis_llm = llm_manager.initialize_standalone_service("github_copilot")
    analyze = CodeAnalysisService(code_analysis_llm)
    path = "./"
    feature_scope = "a2a server"
    result = await analyze.analyze_code_structure(
        path, exclude_patterns=["*.js"], feature_scope=feature_scope
    )
    print(result)

    if isinstance(result, dict):
        raise Exception(f"Failed to analyze code: {result.get('error', '')}")

    project_notes = await analyze.extract_project_notes(
        result, path, feature_scope=feature_scope
    )

    print(project_notes)
    token_count = count_tokens(json.dumps(result))
    print(f"\nToken count: {token_count}")


if __name__ == "__main__":
    asyncio.run(main())
