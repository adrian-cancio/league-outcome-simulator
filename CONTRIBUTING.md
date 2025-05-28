# Contributing to League Outcome Simulator

Thank you for your interest in contributing to League Outcome Simulator! All contributions are welcome, whether it's reporting a bug, suggesting an improvement, or submitting code.

## Table of Contents
- [Contributing to League Outcome Simulator](#contributing-to-league-outcome-simulator)
  - [Table of Contents](#table-of-contents)
  - [Code of Conduct](#code-of-conduct)
  - [How to Get Started](#how-to-get-started)
    - [Prerequisites](#prerequisites)
    - [Environment Setup](#environment-setup)
  - [How to Contribute](#how-to-contribute)
    - [Reporting Bugs](#reporting-bugs)
    - [Suggesting Enhancements](#suggesting-enhancements)
    - [Pull Request Process](#pull-request-process)
  - [Coding Standards](#coding-standards)
    - [Python](#python)
    - [Rust](#rust)
    - [Commit Messages](#commit-messages)
  - [Development Workflow](#development-workflow)
  - [Updating Documentation and Dependencies](#updating-documentation-and-dependencies)
  - [License](#license)

## Code of Conduct
This project and everyone participating in it is governed by a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior. (Note: You may need to create a `CODE_OF_CONDUCT.md` file, perhaps using the Contributor Covenant template.)

## How to Get Started

### Prerequisites
Ensure you have all the prerequisites listed in the [Prerequisites](README.md#prerequisites) section of the `README.md` file. This includes Python, the Rust toolchain, and OS-specific build tools.

### Environment Setup
1.  **Fork the repository** to your GitHub account.
2.  **Clone your fork locally**:
    ```bash
    git clone https://github.com/your-username/league-outcome-simulator.git
    cd league-outcome-simulator
    ```
3.  **Create a virtual environment for Python and install dependencies**:
    ```bash
    python -m venv venv
    # On Windows (pwsh or cmd):
    # .\venv\Scripts\Activate.ps1
    # or
    # .\venv\Scripts\activate.bat
    # On macOS/Linux (bash/zsh):
    # source venv/bin/activate
    
    pip install -r requirements.txt
    ```
4.  **Rust Extension Compilation**:
    The Rust extension (`league_outcome_simulator_rust`) is automatically compiled the first time you import or run the simulator via the Python wrapper (`league_outcome_simulator/simulation.py`).
    If you wish to compile it manually (e.g., during Rust library development), you can run:
    ```bash
    cargo build --release
    ```
    This will place the build artifacts in the `target/release/` directory. The Python wrapper in `simulation.py` is configured to find the compiled library.

## How to Contribute

### Reporting Bugs
If you find a bug, please open an "Issue" on the main GitHub repository. Include the following information:
-   A clear and concise description of the bug.
-   Steps to reproduce the bug.
-   Expected behavior.
-   Actual behavior.
-   Your environment (operating system, Python version, Rust version).
-   Any relevant error messages or tracebacks.

### Suggesting Enhancements
If you have ideas for new features or improvements, open an "Issue" on GitHub. Describe your suggestion in detail:
-   What problem does it solve or what value does it add?
-   How do you envision it working?
-   Any alternatives or additional considerations.

### Pull Request Process
1.  **Ensure your fork is up to date** with the upstream `main` branch.
2.  **Create a new branch** for your feature (`git checkout -b feature/FeatureName`) or bugfix (`git checkout -b bugfix/BugDescription`).
3.  **Make your changes** and write descriptive commit messages (see [Commit Messages](#commit-messages)).
4.  **Ensure your code adheres to the standards** (see [Coding Standards](#coding-standards)).
5.  **Push your changes** to your fork (`git push origin feature/FeatureName`).
6.  **Open a Pull Request (PR)** against the `main` branch of the original repository.
    -   Provide a clear description of the changes in the PR.
    -   If the PR closes an existing issue, reference the issue (e.g., `Closes #123`).
    -   Ensure your PR passes any automated checks/CI builds.

## Coding Standards

### Python
-   Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) guidelines.
-   Use descriptive variable and function names.
-   Add comments to explain complex logic.
-   Utilize established libraries like `pandas` for data manipulation, `numpy` for numerical tasks, and `selenium` for web interactions in `data.py` appropriately.
-   Follow best practices for `pyo3` when interacting with the Rust layer in `simulation.py`.
-   Implement robust error handling, especially for API calls (e.g., in `data.py`) and complex calculations. Provide informative error messages.

### Rust
-   Write idiomatic Rust code.
-   Use `rustfmt` to format your code (`cargo fmt`).
-   Pay attention to performance and memory safety.
-   Ensure code in `src/lib.rs` interfaces correctly with Python via `pyo3`.
-   Implement robust error handling.

### Commit Messages
-   Write clear and concise commit messages.
-   Consider following the [Conventional Commits](https://www.conventionalcommits.org/) convention if you are familiar with it.
-   Example: `feat: Add probability calculation for draws` or `fix: Correct error in SofaScore data parsing`.

## Development Workflow
-   Respect the existing modular design. Functions and classes should have clear responsibilities.
-   Maintain a clear separation of concerns between modules (e.g., data fetching in `data.py`, simulation logic in Rust and `simulation.py`, CLI in `cli.py`).
-   **Prioritize leveraging the Rust backend (`src/lib.rs`) for computationally intensive simulation tasks.**

## Updating Documentation and Dependencies
-   If your changes affect the project's setup, usage, or overall architecture, **update the `README.md` file** to reflect these modifications.
-   If your changes involve adding, removing, or altering dependencies:
    -   For Python, update `requirements.txt`.
    -   For Rust, update `Cargo.toml`.

## License
By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE) that covers the project.

Thank you for contributing!
