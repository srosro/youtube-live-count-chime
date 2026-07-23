test:
    uv run --no-project --with-editable . --with 'mypy>=1.10,<2' python -m unittest discover -s tests -v
    uv run --no-project --with-editable . --with 'mypy>=1.10,<2' mypy --strict src tests
