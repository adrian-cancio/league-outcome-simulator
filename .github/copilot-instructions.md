# Copilot Instructions

## Programming Language
Use Python for all code examples.

## Communication
Explain the code and logic in English, but keep responses concise and clear.

## Style
- Follow PEP 8 guidelines for Python code.
- Use descriptive variable and function names.
- Add comments to explain complex logic.

## Efficiency
- Strive for the most efficient solutions and algorithms.
- Minimize redundant operations and optimize resource usage.
- **Prioritize leveraging the Rust backend (`src/lib.rs`) for computationally intensive simulation tasks.**

## Scope
- Focus on football-related problems or probabilities.
- Avoid unrelated topics or overly complex solutions.

## Output
Provide complete, functional code snippets when possible.

## Tone
Maintain a professional and helpful tone.

## Technical Focus
- **Python:**
    - Utilize established libraries like `pandas` for data manipulation, `numpy` for numerical tasks, and `selenium` for web interactions in `data.py` appropriately.
    - Follow best practices for `pyo3` when interacting with the Rust layer in `simulation.py`.
- **Rust:**
    - When working with `src/lib.rs`, ensure code is idiomatic Rust and interfaces correctly with Python via `pyo3`.
    - Pay attention to performance and memory safety.
- **Error Handling:**
    - Implement robust error handling, especially for API calls (e.g., in `data.py`) and complex calculations. Provide informative error messages.
- **Modularity:**
    - Respect the existing modular design. Functions and classes should have clear responsibilities.

## Project Structure
It is very important to maintain the project structure.
If you need to create a new module, package, or file (for example, a "humor" component or similar), create it following folder conventions and place it in the appropriate directory.
Each part must reside in its designated location to ensure consistency and facilitate maintenance.
- **Maintain clear separation of concerns between modules (e.g., data fetching in `data.py`, simulation logic in Rust and `simulation.py`, CLI in `cli.py`).**

## Post-Change Verification
- After implementing any code changes or new features, always review the `README.md` file.
- If the changes affect the project's setup, usage, or overall architecture, update the `README.md` accordingly to reflect these modifications.
- **If changes involve adding, removing, or altering dependencies, ensure `requirements.txt` (for Python) and/or `Cargo.toml` (for Rust) are updated accordingly.**
