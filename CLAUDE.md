# Desmata Development Guide

## Build/Test Commands
- Install dependencies: `poetry install`
- Run all tests: `poetry run pytest`
- Run single test with logs: `poetry run pytest -s test/test_file.py::test_name`
- Build package: `poetry build`
- Enter Nix dev environment: `nix develop`

## Code Style
- **Imports**: stdlib → third-party → local (alphabetical within groups)
- **Naming**: CamelCase for classes, snake_case for functions/variables, UPPER_SNAKE_CASE for constants
- **Types**: Use type annotations throughout; Pydantic for models
- **Architecture**: Protocols in higher/lower_protocols.py, dependency injection with injector
- **Error handling**: Custom exceptions in exceptions.py
- **Documentation**: Use reStructuredText format docstrings

## Project Structure
Desmata is a package manager that uses content-addressing and reproducible builds through Nix to manage dependencies.