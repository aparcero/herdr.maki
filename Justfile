set shell := ["bash", "-euo", "pipefail", "-c"]
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

# Exercise the installed package with Maki, ast-grep, and a loopback model.
[unix]
check:
    python3 -m unittest discover -s tests -p 'test_*.py'

[windows]
check:
    python -m unittest discover -s tests -p 'test_*.py'
