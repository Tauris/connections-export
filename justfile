# HCL Connections Export — common tasks. See https://just.systems
# `just` lists recipes; `just <recipe>` runs one.

# Recipes that only make sense where this checkout lives. Optional: the file
# is absent in a distribution, and `import?` is silent about that.
import? 'private.just'

# List available recipes
default:
    @just --list

# Create/sync the venv and install dependencies
sync:
    uv sync

# Run the test suite (pass args through, e.g. `just test tests/derive -k links`)
test *args:
    uv run pytest {{args}}

# Lint
lint:
    uv run ruff check .

# Auto-format the code
fmt:
    uv run ruff format .

# Everything CI checks: lint, format-check, and the full suite
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run pytest

# Watch a real crawl in the browser (real pipeline vs. the synthetic server).
# Auto-picks a free port if the given one is busy; `just demo 8137` to choose.
demo port="8000":
    uv run connections-export serve --demo --port {{port}}

# Install the headless browser Playwright needs for PDF export (Path A)
browser:
    uv run playwright install chromium

# Just otherwise looks for `sh` on Windows. Keep the normal POSIX shell on
# Unix-like systems, but use the inbox Windows PowerShell for recipes there.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
