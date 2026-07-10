# Contributing to AgentCrew

Thank you for your interest in contributing to AgentCrew! 🎉 We're excited to
collaborate with you and appreciate contributions of all kinds, no matter how
small. AgentCrew is a community-driven project, and your participation helps
make it better for everyone.

## 🌟 Ways to Contribute

We welcome various types of contributions:

- **Bug Reports**: Help us identify and fix issues
- **Feature Requests**: Suggest new functionality or improvements
- **Code Contributions**: Fix bugs, implement features, or improve performance
- **Documentation**: Improve guides, add examples, or fix typos
- **Testing**: Write tests, report test failures, or improve test coverage
- **UI/UX Improvements**: Enhance the GUI or CLI experience
- **Agent Development**: Create new agents or improve existing ones
- **Tool Integration**: Add support for new tools and services

## 🚀 Getting Started

### Prerequisites

- Python 3.8 or higher
- Git
- Basic understanding of AI/LLM concepts (helpful but not required)

### Development Environment Setup

1. **Get the code:**

   ```bash
   git clone https://github.com/saigontechnology/AgentCrew.git
   cd AgentCrew
   ```

2. **Set up a Python environment:**

   ```bash
   uv sync
   uv run AgentCrew/main.py chat
   ```

3. **Configure API Keys** (for testing)
   - Copy the example configuration file
   - Add your test API keys (use test/sandbox keys when possible)

### Running Tests

```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest tests/test_specific_module.py

# Run with coverage
python -m pytest --cov=AgentCrew --cov-report=html
```

### Code Quality Checks

Before submitting your contribution, ensure your code passes quality checks:

```bash
# Code formatting (if using black)
black AgentCrew/ tests/

# Linting (if using flake8 or ruff)
flake8 AgentCrew/ tests/

# Type checking (if using mypy)
mypy AgentCrew/
```

## 🐛 Reporting Issues

When reporting bugs, please include:

- **Clear Description**: What you expected vs. what happened
- **Steps to Reproduce**: Detailed steps to recreate the issue
- **Environment Details**: OS, Python version, AgentCrew version
- **Error Messages**: Full error traceback if available
- **Configuration**: Relevant parts of your config (remove sensitive data)

**Use our issue template when available** - it helps us understand and resolve
issues faster.

## 💡 Suggesting Features

For feature requests:

- **Check existing issues** first to avoid duplicates
- **Describe the problem** your feature would solve
- **Explain your proposed solution** with examples if possible
- **Consider alternatives** and mention any you've explored
- **Discuss impact** on existing functionality

## 🔧 Development Guidelines

### Code Style

- Follow **PEP 8** Python style guidelines
- Use meaningful variable and function names
- Keep functions focused and reasonably sized
- Add docstrings to classes and functions
- Use type hints where appropriate

### Architecture Principles

- **Modularity**: Keep components loosely coupled
- **Single Responsibility**: Each class/function should have one clear purpose
- **EventBus**: Publish events through the application-owned `EventBus` using `AppEvents` constants and normalized keyword payloads documented in `EVENT_PAYLOAD_MAP`
- **Hooks**: Register `tool.execute` hooks through the application-owned `HookRegistry`; only `before` and `after` phases are supported
- **Service-Oriented**: Follow the existing service-based architecture
- **Configuration-Driven**: Make features configurable when possible

### Adding New Features

1. **Discuss First**: For significant features, open an issue to discuss the
   approach
2. **Follow Patterns**: Study existing code patterns (agents, tools, services)
3. **Update Documentation**: Include docstrings and update relevant docs
4. **Add Tests**: Include unit tests for new functionality
5. **Update Configuration**: Add any new config options to the schema

### Working with Agents

When creating or modifying agents:

- Extend `BaseAgent` or `LocalAgent` appropriately
- Implement all required methods
- Follow the established tool registration pattern
- Add appropriate error handling
- Test with multiple LLM providers when possible

### Working with Tools

For new tools:

- Create tool definition and handler functions
- Follow the existing tool registration pattern
- Include proper input validation
- Add comprehensive error handling
- Document tool capabilities and limitations

### GUI Development

For Qt GUI improvements:

- Follow the existing component-based structure
- Maintain consistency with current styling
- Test across different screen sizes
- Ensure accessibility considerations
- Update relevant themes if adding new UI elements

## 📝 Pull Request Process

### Before Submitting

- [ ] Fork the repository and create a feature branch
- [ ] Write clear, descriptive commit messages
- [ ] Ensure all tests pass
- [ ] Update documentation if needed
- [ ] Add tests for new functionality
- [ ] Check that your changes don't break existing features

### Pull Request Guidelines

1. **Use a Clear Title**: Summarize what your PR does
2. **Describe Changes**: Explain what you changed and why
3. **Reference Issues**: Link to related issues using "Fixes #123"
4. **Add Screenshots**: For UI changes, include before/after screenshots
5. **List Breaking Changes**: Highlight any breaking changes
6. **Update Changelog**: Add entry to CHANGELOG.md if one exists

### Review Process

- PRs require review from maintainers
- Address feedback promptly and constructively
- Be patient - reviews take time, especially for complex changes
- Feel free to ask questions if feedback isn't clear

## 🧪 Testing Guidelines

### Test Coverage

- Aim for high test coverage on new code
- Include both unit tests and integration tests where appropriate
- Test error conditions and edge cases
- Mock external services (APIs, databases) in tests

### Test Organization

- Place tests in the `tests/` directory
- Mirror the source code structure in test files
- Use descriptive test names that explain what is being tested
- Group related tests in test classes

### Test Types

- **Unit Tests**: Test individual functions/methods in isolation
- **Integration Tests**: Test component interactions
- **End-to-End Tests**: Test complete workflows (use sparingly)
- **API Tests**: Test tool integrations and external service calls

## 📚 Documentation

### Types of Documentation

- **Code Documentation**: Docstrings, inline comments
- **User Documentation**: README, installation guides, tutorials
- **API Documentation**: Tool definitions, service interfaces
- **Developer Documentation**: Architecture notes, design decisions

### Documentation Standards

- Use clear, concise language
- Include code examples where helpful
- Keep documentation up-to-date with code changes
- Use proper Markdown formatting
- Include links to relevant resources

## 🤝 Community Guidelines

### Communication

- Be respectful and constructive in all interactions
- Use inclusive language
- Help newcomers get started
- Share knowledge and learn from others
- Follow our [Code of Conduct](CODE_OF_CONDUCT.md)

### Getting Help

- **GitHub Discussions**: For questions and general discussion
- **Issues**: For bug reports and feature requests
- **Discord/Slack**: For real-time chat (if available)

## 🎯 Good First Issues

Look for issues labeled:

- `good first issue`: Perfect for newcomers
- `help wanted`: We'd love community help on these
- `documentation`: Improve docs and examples
- `tests`: Add or improve test coverage

## 📋 Development Workflow

### Branching Strategy

- `main`: Stable, production-ready code
- `develop`: Integration branch for new features (if used)
- `feature/description`: Feature development branches
- `bugfix/description`: Bug fix branches
- `hotfix/description`: Critical production fixes

### Commit Messages

Use clear, descriptive commit messages:

```
feat: add support for custom LLM providers
fix: resolve memory leak in conversation manager
docs: update installation instructions for Python 3.12
test: add unit tests for clipboard service
refactor: simplify agent registration process
```

### Release Process

- Maintainers handle releases
- Follow semantic versioning (MAJOR.MINOR.PATCH)
- Update version numbers and changelog
- Create release notes with highlights

## 🔒 Security

- **Never commit sensitive data** (API keys, passwords, personal info)
- **Report security issues privately** to maintainers
- **Use environment variables** for configuration secrets
- **Follow security best practices** in code contributions

## 📄 License

By contributing to AgentCrew, you agree that your contributions will be licensed
under the same license as the project.

## 🙏 Recognition

We appreciate all contributors! Contributors will be:

- Listed in our contributors section
- Mentioned in release notes for significant contributions
- Invited to join our contributor community

---

## Questions?

Don't hesitate to ask! We're here to help you contribute successfully to
AgentCrew. You can:

- Open an issue with the `question` label
- Start a discussion in GitHub Discussions
- Reach out to maintainers directly

**Thank you for helping make AgentCrew better! 🚀** Community leaders have the
right and responsibility to remove, edit, or reject comments, commits, code,
wiki edits, issues, and other contributions
