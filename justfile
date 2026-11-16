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

# Create/sync the venv with Windows integrated-auth support
sync-sspi:
    uv sync --extra sspi

# Run the test suite (pass args through, e.g. `just test tests/derive -k links`)
test *args:
    uv run pytest {{args}}

# Walk the console's key journeys in a real browser (needs Chromium)
journeys:
    uv run pytest tests/e2e -p no:cacheprovider

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

# The console, in a browser. Drop one of the demo URLs it offers on the setup
# screen to watch the real pipeline run against the synthetic server -- which
# deployment gets read follows from the URL, so there is no mode to set here.
# Auto-picks a free port if the given one is busy; `just console 8137` to choose.
#
# Signing in as yourself needs the `sspi` extra. On Linux (WSL included) that
# extra compiles Kerberos against the system's headers; without them the
# install fails and the console never starts. So there it is used only when
# the headers are present -- otherwise the console starts without it and says
# how to add it (`sudo apt-get install libkrb5-dev`). Demo data and archives
# need no sign-in. Windows and macOS have prebuilt packages and always get it.
#
# Start the console in your browser (`just console 8137` to choose the port)
[unix]
console port="8000":
    if [ "$(uname)" = Darwin ] || command -v krb5-config >/dev/null 2>&1; then \
        uv run --extra sspi connections-export serve --open --port {{port}}; \
    else \
        echo "Note: Kerberos headers not found, so signing in as yourself is off for this run." >&2; \
        echo "      Demo data and archives work. To capture with your own sign-in:" >&2; \
        echo "      sudo apt-get install libkrb5-dev   (Fedora/RHEL: sudo dnf install krb5-devel)" >&2; \
        uv run connections-export serve --open --port {{port}}; \
    fi

[windows]
console port="8000":
    uv run --extra sspi connections-export serve --open --port {{port}}

# Install the headless browser Playwright needs for PDF export (Path A)
browser:
    uv run playwright install chromium

# Just otherwise looks for `sh` on Windows. Keep the normal POSIX shell on
# Unix-like systems, but use the inbox Windows PowerShell for recipes there.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
