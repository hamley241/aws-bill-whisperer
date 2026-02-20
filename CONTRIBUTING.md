# Contributing to AWS Bill Whisperer

Thanks for your interest in contributing! This document outlines how to get started and what to expect.

## Quick Start

1. **Fork the repo**
2. **Clone your fork**: `git clone https://github.com/YOUR_USERNAME/aws-bill-whisperer.git`
3. **Install dependencies**: `pip install -e .[dev]`
4. **Run tests**: `pytest`
5. **Create a branch**: `git checkout -b feature/my-feature`

## Development Setup

```bash
# Clone and install
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check .
ruff format .
mypy src/

# Test CLI locally
python -m aws_bill_whisperer --mock
```

## Project Structure

- `src/` - Lambda function source code
- `cli/` - Standalone CLI tool
- `tests/` - Unit tests
- `template.yaml` - SAM/CloudFormation template
- `examples/` - Sample outputs and docs

## What We're Looking For

- 🐛 Bug fixes
- ✨ New features (check Roadmap in README)
- 📚 Documentation improvements  
- 🧪 Tests for uncovered code
- 🎨 Better prompts for cost analysis

## Pull Request Process

1. Ensure tests pass (`pytest`)
2. Update documentation if needed
3. Add yourself to CONTRIBUTORS (optional)
4. Open a PR with a clear description

## Code Style

- PEP 8
- Type hints encouraged
- Docstrings for public functions
- Keep it simple

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src

# Test CLI with mock data (no AWS needed)
python cli/analyze.py --mock
```

## Commit Messages

- Keep them descriptive
- Reference issues when applicable
- One logical change per commit

Example:
```
Add cost anomaly detection for sudden spikes

- Implements anomaly detection using standard deviation
- Adds --anomaly-threshold CLI argument
- Closes #42
```

## Questions?

Open an issue or reach out to the maintainers.

---

By contributing, you agree that your contributions will be licensed under the MIT License.
