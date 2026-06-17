## About logic-cov

**logic-cov** (Logic Coverage) is a pragmatic static analysis tool designed specifically for developers refactoring legacy or hybrid Python applications (especially those built with Tkinter, PyQt, or PySide). 

Traditional coverage tools like `pytest-cov` only measure *execution*—they tell you which lines of code were interpreted during a test run, but they cannot distinguish between UI layout boilerplate and critical backend code. This often leads to a "false 100% coverage trap," where your GUI components are loaded, but your core business logic remains unchecked.

**logic-cov** solves this by analyzing the Abstract Syntax Tree (AST) of your codebase to calculate **Target Density** and pinpoint exactly where your pure Python logic resides. By filtering out the noise of UI widget configurations (`pack`, `grid`, `bind`, etc.), it generates a surgical roadmap of precisely which line numbers contain untested, test-worthy business logic.

### Key Features

* 📊 **Three Verbosity Levels:** Switch easily from a high-level codebase overview to a dedicated coverage-style report or an ultra-detailed per-function analysis.
* 🎯 **Smart Logic Triage (`Target%`):** Automatically classifies functions into `GUI`, `LOGIC`, `MIXED`, or `UNKNOWN` based on heuristics and keyword clustering.
* 🩺 **Pytest-Cov Style Output:** Generates clear, tabular terminal reports that list exact line-number ranges (`Untested Logic Lines`) so you know exactly where to write your next unit test.
* 🚀 **Zero Configuration & Zero Dependencies:** Runs natively using Python's built-in `ast` module. No heavy external servers or complex CI setups required.
