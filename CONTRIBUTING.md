# Contributing

Thank you for considering an improvement to Blender Production Automation.

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the quality checks before submitting a change:

```powershell
python -m ruff format .
python -m ruff check .
python -m pytest
```

## Pull requests

- Keep each pull request focused on one change.
- Add tests for new or corrected behavior.
- Explain operational impact and compatibility considerations.
- Use fictional fixtures only; never include production assets or customer data.
- Do not add generated-by text or AI co-author attribution to commit messages.

By contributing, you agree that your contribution is licensed under the MIT License.
