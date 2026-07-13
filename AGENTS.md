# Repository Guidance

This repository contains public, reusable automation code. Production assets,
customer mappings, private URLs, and machine-specific paths must remain outside
version control.

## Development

- Support Python 3.10 through 3.14.
- Put reusable Python code under `src/blender_production_automation`.
- Keep destructive file operations preview-only unless the user explicitly opts in.
- Add or update tests for behavior changes.

## Verification

Run these checks before committing:

```powershell
python -m compileall -q src
python -m pytest
python -m ruff format --check .
python -m ruff check .
```

## Privacy and Git hygiene

- Never commit real customer names, customer URLs, Drive identifiers, credentials,
  production PDFs, Blender files, renders, labels, or local routing configuration.
- Use fictional data under `config/examples` and tests.
- Review staged files with `git diff --cached` before every commit.
- Do not add AI attribution, generated-by notices, or AI `Co-authored-by` trailers
  to commits or pull requests.
