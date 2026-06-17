# logic-cov

`logic-cov` (Logic Coverage) is a pragmatic static analysis tool designed to uncover untested core business logic hidden inside complex Python GUI and hybrid applications. 

Traditional coverage tools like `pytest-cov` only measure **execution path coverage**—they tell you which lines of code were interpreted during a test run, but they cannot distinguish between UI layout boilerplate and critical backend operations. This often leads to a "false 100% coverage trap," where your GUI components are loaded into memory, but your critical data processing remains untested.

`logic-cov` solves this by analyzing the Abstract Syntax Tree (AST) of your codebase to calculate **Target Density** and pinpoint exactly where your pure Python logic resides. By filtering out the noise of UI widget configurations (`pack`, `grid`, `bind`, etc.), it generates a surgical roadmap of precisely which line numbers contain untested, test-worthy business logic.

---

## Key Features

* 📊 **Three Level Triage:** Switch seamlessly from a high-level codebase overview to a dedicated coverage-style line report or an ultra-detailed per-function analysis.
* 🎯 **Aesthetic vs. Logic Classification:** Automatically classifies functions into `GUI`, `LOGIC`, `MIXED`, or `UNKNOWN` using semantic weighting and keyword clustering.
* 🩺 **Pytest-Cov Style Line Matching:** Generates clear, tabular terminal reports that list exact line-number ranges (`Untested Logic Lines`) so you know exactly where to write your next unit test.
* 🚀 **Zero Configuration & Zero Dependencies:** Built entirely on top of Python's native `ast` and `pathlib` modules. No heavy external servers, Docker containers, or complex CI pipelines required.

---

## How It Works Under the Hood

The tool statically parses your `.py` files without executing them. It utilizes Python's `ast.NodeVisitor` to inspect:
1. **Function Names:** Functions containing keywords like `build_`, `show_`, or `ui_` gain GUI weight, while `calc_`, `parse_`, or `load_` gain Logic weight.
2. **Function Calls:** Internal method invocations (e.g., calling `.grid()`, `.pack()`, or instantiating widgets like `ttk.Notebook`) are treated as GUI indicators. System and file operations (e.g., using `json`, `subprocess`, `os`, `re`, or `open`) shift the weight toward Logic.

### Architectural Scoring Heuristics
A mathematical ratio determines final category assignments:
* **UNKNOWN:** Total weight score is exactly `0` (typically empty methods, raw properties, or abstract definitions).
* **GUI / LOGIC:** One type heavily dominates the other by a factor of at least $1.5\times$ (or one score is completely `0`).
* **MIXED:** High scores on both sides that fail to satisfy the $1.5\times$ threshold margin, signaling tight coupling between the UI and logic.

---

## CLI Usage

`logic-cov` is designed to be plug-and-play. It automatically scans the `scripts/` directory for Python files and offers three levels of granularity depending on your current objective.

Run the tool from your project root directory:

```bash
python3 logic-cov.py [flags]


1. Defaut mode (Codebase Summary Overview):
Command: python3 logic-cov.py

Best for a quick, high-level health check of your codebase architecture. It outputs a clean, compact table showing the percentage distribution of code categories based on function body lengths, concluded by a global codebase summary.

Example Output:
===============================================================================================
NAZWA PLIKU                              | GUI        | LOGIC      | MIXED      | UNKNOWN   
===============================================================================================
scripts/glava-gui.py                     |      37.2% |      32.9% |      29.3% |       0.5%
scripts/gui/colors.py                    |       0.0% |      80.4% |       0.0% |      19.6%
-----------------------------------------------------------------------------------------------
SUMA GLOBALNA                            |      43.8% |      41.9% |       7.5% |       6.8%
===============================================================================================

2. Verbose mode -v (Logic Coverage Report):
Command: python3 logic-cov.py -v

Modeled after pytest-cov, this is your primary testing roadmap. It displays Target Density (Target%), showing what percentage of your functions contain test-worthy backend logic, along with the exact physical file line numbers where they live.

Example Output:
---------- logic test-targets: platform analysis -----------
Name                                              Stmts   Logic Target%  Untested Logic Lines
-----------------------------------------------------------------------------------------------
scripts/glava-gui.py                               1066     736    69%  76-95, 98-101, 109-142
scripts/gui/colors.py                               173     160    92%  32-56, 58-102, 104-122
-----------------------------------------------------------------------------------------------
TOTAL                                              6118    3308    54%

==================== target scanning finished ====================

3. Double Verbose Mode -vv (Deep Function Dump)
Command: python3 logic-cov.py -vv

The ultimate debugging mode. It performs a deep static inspection and dumps every single function found in your files. For each function, it displays its architectural classification along with its exact heuristic scoring weights (gui= and logic=), followed by the default summary table.

Example Output:
==================== verbose function dump ====================

FILE: scripts/gui/colors.py
  ------------------------------------------------------------
    LOGIC   | gui=0   logic=3   | hex_to_vec3
    LOGIC   | gui=0   logic=3   | vec3_to_hex
    UNKNOWN | gui=0   logic=0   | _clamp01

==================== end of function dump ====================

