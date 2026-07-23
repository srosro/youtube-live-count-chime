test:
    uv run --no-project --with-editable '.[dev]' python -m unittest discover -s tests -v
    uv run --no-project --with-editable '.[dev]' mypy
