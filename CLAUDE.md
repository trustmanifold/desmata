# Desmata Development Guide

## Build/Test Commands
Dependencies are managed with uv and packaged for Nix via uv2nix.
- Enter dev environment: `nix develop` (or `direnv allow` / `use flake`)
- Update/lock dependencies: `uv lock` (after editing `pyproject.toml`)
- Run all tests: `pytest` (inside the dev shell)
- Run single test with logs: `pytest -s test/test_file.py::test_name`
- Build package (venv): `nix build`

## Code Style
- **Imports**: stdlib → third-party → local (alphabetical within groups)
- **Naming**: CamelCase for classes, snake_case for functions/variables, UPPER_SNAKE_CASE for constants
- **Types**: Use type annotations throughout; Pydantic for models
- **Architecture**: Protocols in higher/lower_protocols.py, dependency injection with injector
- **Content addressing**: All hashes are the self-describing `Hash` model from content.py (`dsm:<backend>:<digest>`, IPFS-only today); backends implement the `ContentBackend` protocol and dispatch goes through `BackendRegistry` — don't pass bare CID strings across module boundaries
- **Error handling**: Custom exceptions in exceptions.py
- **Documentation**: Use reStructuredText format docstrings

## Project Structure
Desmata is a package manager that uses content-addressing and reproducible builds through Nix to manage dependencies.