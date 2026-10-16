# Contributing

Thanks for helping improve this beginner Applied AI project.

## Good First Issues

- Improve the sample knowledge base.
- Add more evaluation questions.
- Add another retrieval scoring strategy.
- Add a GitHub Actions test workflow.
- Add a streaming endpoint.

## Local Development

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

## Pull Request Checklist

- Tests pass with `pytest`.
- README or docs are updated if behavior changed.
- New AI behavior includes a small evaluation example.
