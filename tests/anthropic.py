import os
from unittest.mock import Mock, patch

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock

from AgentCrew.modules.anthropic import SUMMARIZE_PROMPT, AnthropicClient


@pytest.fixture
def mock_anthropic():
    with patch("modules.anthropic.Anthropic") as mock:
        yield mock


@pytest.fixture
def mock_env_vars():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        yield


def test_anthropic_client_init_missing_api_key():
    with (
        patch.dict(os.environ, clear=True),
        pytest.raises(
            ValueError, match="ANTHROPIC_API_KEY not found in environment variables"
        ),
    ):
        AnthropicClient()


def test_anthropic_client_init_success(mock_env_vars):
    client = AnthropicClient()
    assert client is not None


def test_summarize_content_success(mock_anthropic, mock_env_vars):
    # Arrange
    test_content = "Test markdown content"
    expected_summary = "Summarized content"

    mock_message = Mock(spec=Message)
    mock_text_block = Mock(spec=TextBlock)
    mock_text_block.text = expected_summary
    mock_message.content = [mock_text_block]

    mock_client = mock_anthropic.return_value
    mock_client.messages.create.return_value = mock_message

    # Act
    client = AnthropicClient()
    result = client.summarize_content(test_content)

    # Assert
    assert result == expected_summary
    mock_client.messages.create.assert_called_once_with(
        model="claude-3-5-sonnet-latest",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": SUMMARIZE_PROMPT.format(content=test_content),
            }
        ],
    )


def test_summarize_content_api_error(mock_anthropic, mock_env_vars):
    # Arrange
    mock_client = mock_anthropic.return_value
    mock_client.messages.create.side_effect = Exception("API Error")

    # Act & Assert
    client = AnthropicClient()
    with pytest.raises(Exception, match="Failed to summarize content: API Error"):
        client.summarize_content("test content")


def test_summarize_content_invalid_response(mock_anthropic, mock_env_vars):
    # Arrange
    mock_message = Mock(spec=Message)
    mock_invalid_block = Mock(spec=ToolUseBlock)  # Not a TextBlock
    mock_message.content = [mock_invalid_block]

    mock_client = mock_anthropic.return_value
    mock_client.messages.create.return_value = mock_message

    # Act & Assert
    client = AnthropicClient()
    with pytest.raises(
        Exception,
        match="Failed to summarize content: Unexpected response type: message content is not a TextBlock",
    ):
        client.summarize_content("test content")
