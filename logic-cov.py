#!/usr/bin/env python3

import ast
import sys
import argparse
from pathlib import Path
import subprocess
import re
import os

# --- Metric models ---
METRIC_LINE = "line"
METRIC_STRUCT = "struct"

# --- Key words ---
# GUI_HINTS: metody/atrybuty będące silnymi wskaźnikami warstwy UI.
# Celowo wąski zestaw — usunieto "config" (pliki konfiguracji), "update" (dict/set/logika)
# i "window" (zmienna, nie wywołanie) bo generowały fałszywe alarmy GUI w plikach logiki.
GUI_HINTS = {
    "pack", "grid", "place", "bind", "configure",
    "StringVar", "BooleanVar", "IntVar", "DoubleVar", "PhotoImage",
    "mainloop", "Tk", "title", "geometry", "after", "destroy",
    "focus_set", "menu", "style", "theme"
}

GUI_WIDGETS = {
    "Button", "Frame", "Label", "Checkbutton", "Combobox", "Notebook", 
    "Entry", "Scale", "Separator", "Scrollbar", "Canvas", "Menu", "Toplevel",
    "LabelFrame", "TFrame", "TLabel", "TCheckbutton", "TEntry", "TSeparator"
}

LOGIC_HINTS = {
    "open", "json", "os", "subprocess", "threading", "re", "shutil", "pathlib",
    "print", "sys", "exit", "read", "write", "dump", "load", "exec", "eval",
    "append", "split", "join", "match", "search", "communicate", "Popen"
}

GUI_NAME_HINTS = ["build", "show", "hide", "ui", "widget", "panel", "frame", "tab", "window", "render"]
LOGIC_NAME_HINTS = ["load", "save", "read", "write", "calc", "fetch", "get", "set", "update", "check", "parse", "process"]

# Metody magiczne Pythona realizujące czystą logikę strukturalną/porównawczą
MAGIC_LOGIC_METHODS = {
    "__init__", "__str__", "__repr__", "__eq__", "__ne__", 
    "__lt__", "__le__", "__gt__", "__ge__", "__hash__", 
    "__enter__", "__exit__", "__call__", "__iter__", "__next__"
}

class Analyzer(ast.NodeVisitor):
    def __init__(self, func_name="", has_gui_imports=False):
        self.gui = 0
        self.logic = 0
        self.has_control_flow = False
        self.has_gui_imports = has_gui_imports
        
        func_name_lower = func_name.lower()
      
        # Jeśli to metoda magiczna - z definicji dajemy bazowe punkty do LOGIC
        if func_name in MAGIC_LOGIC_METHODS:
            self.logic += 2

        # Analiza podpowiedzi w nazwie funkcji z uwzględnieniem kontekstu importów
        if any(hint in func_name_lower for hint in GUI_NAME_HINTS):
            if self.has_gui_imports:
                self.gui += 2
            else:
                self.logic += 1  # Jeśli plik nie importuje GUI, słowa typu 'build' oznaczają logikę
        if any(hint in func_name_lower for hint in LOGIC_NAME_HINTS):
            self.logic += 2

    # Śledzenie struktur sterujących (jeśli występują, funkcja rzadko jest "UNKNOWN")
    def visit_If(self, node):
        self.has_control_flow = True
        self.generic_visit(node)

    def visit_For(self, node):
        self.has_control_flow = True
        self.generic_visit(node)

    def visit_While(self, node):
        self.has_control_flow = True
        self.generic_visit(node)

    def visit_Try(self, node):
        self.has_control_flow = True
        self.generic_visit(node)

    def visit_Call(self, node):
        name = self._name(node.func)
        if name:
            parts = name.split(".")
            short = parts[-1]

            # Weryfikacja GUI z uwzględnieniem kontekstu importów pliku
            if (
                short in GUI_HINTS
                or short in GUI_WIDGETS
                or any(p.lower() in ["tk", "ttk", "theme", "style", "customtkinter"] for p in parts)
            ):
                if self.has_gui_imports or any(p.lower() in ["tk", "ttk", "customtkinter"] for p in parts):
                    self.gui += 1
                else:
                    self.logic += 1

            # Weryfikacja LOGIC
            elif (
                short in LOGIC_HINTS 
                or any(p in LOGIC_HINTS for p in parts)
                or any(p.lower() in ["os", "sys", "subprocess", "json", "pathlib", "re"] for p in parts)
            ):
                self.logic += 1

        self.generic_visit(node)

    def _name(self, node):
        """Safely extracting the full qualified name (e.g., self.button.pack)"""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._name(node.value)
            if base:
                return f"{base}.{node.attr}"
            return node.attr
        if isinstance(node, ast.Call):
            return self._name(node.func)
        return None


def analyze_function(node, has_gui_imports=False):
    a = Analyzer(func_name=node.name, has_gui_imports=has_gui_imports)
    a.visit(node)

    gui = a.gui
    logic = a.logic
    
    # Fallback: jeśli brak jednoznacznych cech, ale jest struktura sterująca, to LOGIC
    if gui == 0 and logic == 0 and a.has_control_flow:
        logic += 1

    total = gui + logic

    if total == 0:
        kind = "UNKNOWN"
    elif gui > 0 and logic == 0:
        kind = "GUI"
    elif logic > 0 and gui == 0:
        kind = "LOGIC"
    elif gui >= logic * 1.5:
        kind = "GUI"
    elif logic >= gui * 1.5:
        kind = "LOGIC"
    else:
        kind = "MIXED"

    return kind, gui, logic


def collect_functions(tree):
    """
    Przechodzi AST i zwraca listę (FunctionDef, is_nested).
    is_nested=True dla funkcji zdefiniowanych wewnątrz innej funkcji (closures).
    Dzięki temu process_file może pominąć osobne zliczanie linii dla closure'ów,
    które są już objęte zakresem funkcji-rodzica.
    """
    result = []

    def _visit(node, inside_func=False):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result.append((child, inside_func))
                _visit(child, inside_func=True)
            else:
                _visit(child, inside_func)

    _visit(tree)
    return result


def build_parent_ctrl_map(tree):
    """
    Buduje mapę linii źródłowej → numer linii nagłówka najbliższej nadrzędnej
    struktury kontrolnej (if, for, while, try, with i ich async-warianty).

    Używana przez tryb -comp do precyzyjnego context padding: zamiast ślepego
    dodawania linii-1 i linii-2, szukamy faktycznego nagłówka bloku, którego
    gałąź nie została przetestowana.

    Przykład:
        10: if condition:
        11:     do_something()  ← brakuje pokrycia
    → parent_ctrl_map[11] = 10  (zamiast dodawać linię 9 i 10 na ślepo)
    """
    _ctrl_types = [ast.If, ast.For, ast.While, ast.Try, ast.With,
                   ast.AsyncFor, ast.AsyncWith]
    # Python 3.10+: match; Python 3.11+: TryStar (try/except*)
    for name in ("Match", "TryStar"):
        node_type = getattr(ast, name, None)
        if node_type:
            _ctrl_types.append(node_type)
    _ctrl_types = tuple(_ctrl_types)

    parent_map = {}

    def _visit(node, ctrl_stack):
        lineno = getattr(node, "lineno", None)
        if lineno is not None and ctrl_stack and lineno not in parent_map:
            parent_map[lineno] = ctrl_stack[-1]
        if isinstance(node, _ctrl_types):
            ctrl_stack = ctrl_stack + (node.lineno,)
        for child in ast.iter_child_nodes(node):
            _visit(child, ctrl_stack)

    _visit(tree, ())
    return parent_map


def merge_ranges(ranges):
    """
    Scala nakładające się lub sąsiadujące przedziały (start, end).
    Zwraca posortowaną listę niepokrywających się przedziałów.
    """
    if not ranges:
        return []
    sorted_ranges = sorted(ranges)
    merged = [list(sorted_ranges[0])]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(r) for r in merged]


def process_file(path):
    """Parses the file only ONCE and gathers all required statistics"""
    try:
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        return None

    # Skonstruowanie kontekstu importów dla pliku (Poziom 1)
    has_gui_imports = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(gui in alias.name.lower() for gui in ["tkinter", "customtkinter", "pyqt", "kivy", "wx"]):
                    has_gui_imports = True
                    break
        elif isinstance(node, ast.ImportFrom):
            if node.module and any(gui in node.module.lower() for gui in ["tkinter", "customtkinter", "pyqt", "kivy", "wx"]):
                has_gui_imports = True
                break
        if has_gui_imports:
            break

    file_results = {
        "path": path,
        "total_func_lines": 0,
        "logic_lines_count": 0,
        "untested_ranges": [],
        "functions_vv": [],
        "f_stats": {"GUI": 0, "LOGIC": 0, "MIXED": 0, "UNKNOWN": 0}
    }

    for node, is_nested in collect_functions(tree):
        kind, gui, logic = analyze_function(node, has_gui_imports=has_gui_imports)

        # Closures zagnieżdżone: uwzględnij w -vv dump, ale pomiń osobne zliczanie linii —
        # ich zakres jest już objęty funkcją-rodzicem, drugie dodanie to błąd.
        if not is_nested:
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", start_line)
            lines_in_func = (end_line - start_line) + 1

            file_results["total_func_lines"] += lines_in_func
            file_results["f_stats"][kind] += lines_in_func

            if kind in ("LOGIC", "MIXED"):
                file_results["logic_lines_count"] += lines_in_func
                file_results["untested_ranges"].append((start_line, end_line))

        file_results["functions_vv"].append(
            f"    {kind:7} | gui={gui:<3} logic={logic:<3} | {node.name}"
        )

    # Scalenie nakładających się zakresów (merge_ranges zwraca już posortowane)
    file_results["untested_ranges"] = merge_ranges(file_results["untested_ranges"])

    # Line range formatting
    range_strings = []
    for start, end in file_results["untested_ranges"]:
        if start == end:
            range_strings.append(f"{start}")
        else:
            range_strings.append(f"{start}-{end}")
    file_results["missing_str"] = ", ".join(range_strings)

    return file_results


def parse_args():
    parser = argparse.ArgumentParser(
        prog="logic-cov",
        description="Statyczny analizator pokrycia logiki dla projektów Python (GUI + logika).",
        epilog=(
            "przykłady:\n"
            "  logic-cov                                        # tabela GUI/LOGIC bieżącego katalogu\n"
            "  logic-cov scripts/                               # tabela dla wskazanego katalogu\n"
            "  logic-cov -v                                     # numery linii logiki do przetestowania\n"
            "  logic-cov -vv                                    # dump punktacji heurystycznej\n"
            "  xvfb-run -a logic-cov tests/ scripts/ -comp      # gap analysis z branch coverage\n"
            "  logic-cov tests/ scripts/ -comp --include-venv   # j.w. + skanowanie venv\n"
        ),
        formatter_class=lambda prog: argparse.RawDescriptionHelpFormatter(prog, width=100)
    )
    parser.add_argument(
        "test_path", nargs="?", default="tests",
        help="katalog z testami pytest, używany tylko z -comp (domyślnie: 'tests')"
    )
    parser.add_argument(
        "src_path", nargs="?", default=".",
        help="katalog z kodem źródłowym (domyślnie: bieżący katalog)"
    )
    parser.add_argument(
        "-v", action="store_true",
        help="procentowy udział logiki i numery linii do przetestowania (Logic Target%%)"
    )
    parser.add_argument(
        "-vv", action="store_true",
        help="dump wszystkich funkcji z surową punktacją heurystyczną gui=X logic=Y"
    )
    parser.add_argument(
        "-comp", action="store_true",
        help="gap analysis: uruchamia pytest --cov --cov-branch i przecina wyniki z mapą logiki; wymaga pytest-cov"
    )
    parser.add_argument(
        "--include-venv", action="store_true",
        help="skanuj też .venv, site-packages i __pycache__ (domyślnie pomijane)"
    )
    return parser.parse_args()


def is_python_file(path: Path) -> bool:
    if path.suffix in {".py", ".pyw"}:
        return True

    try:
        with open(path, "rb") as f:
            first_line = f.readline().decode("utf-8", "ignore").strip()
        return first_line.startswith("#!") and "python" in first_line
    except Exception:
        return False

def collect_files(paths, include_venv=False):
    files = set()

    for item in paths:
        path = Path(item)

        targets = []

        if path.is_file():
            targets = [path]
        elif path.is_dir():
            targets = path.rglob("*")

        for f in targets:
            if not f.is_file():
                continue

            if not include_venv and any(p in f.parts for p in [".venv", "site-packages", "__pycache__"]):
                continue

            if is_python_file(f):
                files.add(f.resolve())

    return sorted(files)


def parse_line_ranges(range_str):
    """
    Parsuje kolumnę Missing z pytest-cov (z --cov-branch lub bez).

    Obsługiwane formaty:
      - "81"         → linia 81
      - "81-90"      → zakres linii 81-90
      - "81->84"     → gałąź z linii 81 do 84 nigdy nie wykonana → dodaj linię 81
      - "81->exit"   → gałąź wyjścia z linii 81 → dodaj linię 81
    """
    lines = set()
    if not range_str.strip():
        return lines
    for part in range_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "->" in part:
            # Adnotacja gałęzi: źródłem jest linia przed "->"
            source = part.split("->")[0]
            try:
                lines.add(int(source))
            except ValueError:
                pass
        elif "-" in part:
            try:
                start, end = map(int, part.split("-", 1))
                lines.update(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                lines.add(int(part))
            except ValueError:
                pass
    return lines


def format_set_to_ranges(line_set):
    if not line_set:
        return ""
    sorted_lines = sorted(list(line_set))
    ranges = []
    start = sorted_lines[0]
    end = sorted_lines[0]
    for line in sorted_lines[1:]:
        if line == end + 1:
            end = line
        else:
            ranges.append(f"{start}" if start == end else f"{start}-{end}")
            start = line
            end = line
    ranges.append(f"{start}" if start == end else f"{start}-{end}")
    return ", ".join(ranges)


def run_and_parse_pytest(test_path, src_path):
    cmd = [
        sys.executable, "-m", "pytest",
        str(test_path),
        "-v",
        f"--cov={src_path}",
        "--cov-branch",
        "--cov-report=term-missing"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    except FileNotFoundError as e:
        print(f"Error: Failed to run pytest command: {' '.join(cmd)}")
        print(str(e))
        sys.exit(1)

    # returncode 0 = all tests passed, 1 = some tests failed (coverage data still valid)
    # anything else = crash / bad invocation
    if result.returncode not in (0, 1):
        print(f"Error: pytest exited with unexpected code {result.returncode}")
        if result.stderr:
            print(f"Pytest Output:\n{result.stderr}")
        sys.exit(1)

    if result.returncode == 1:
        print("[logic-cov] Warning: some tests failed — coverage data may be incomplete.")

    stdout = result.stdout
    coverage_data = {}
    for line in stdout.splitlines():
        if not line.strip() or line.startswith("---") or line.startswith("Name"):
            continue
        if ".py" not in line:
            continue

        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            int(parts[1])
            int(parts[2])
        except ValueError:
            continue

        key = str(Path(parts[0]))  # pełna ścieżka relatywna, normalizacja separatorów

        # Szukaj kolumny Cover% (zawiera '%') — Missing zaczyna się za nią.
        # Działa zarówno bez --cov-branch (format 5-kolumnowy)
        # jak i z nim (format 7-kolumnowy: Stmts Miss Branch BrPart Cover Missing).
        try:
            pct_idx = next(i for i, p in enumerate(parts) if "%" in p)
            missing_str = " ".join(parts[pct_idx + 1:])
        except StopIteration:
            missing_str = ""

        coverage_data[key] = parse_line_ranges(missing_str)
    return coverage_data

def main():
    args = parse_args()
    
    test_path = args.test_path
    src_path = args.src_path

    if args.comp:
        files = collect_files([src_path], include_venv=args.include_venv)
    else:
        files = collect_files([src_path, test_path], include_venv=args.include_venv)

    if not files:
        print("No .py files found in the specified directory.")
        sys.exit(1)

    analyzed_data = []
    for path in files:
        data = process_file(path)
        if data:
            analyzed_data.append(data)

    current_dir = Path.cwd()

    # --- TRYB -comp ---
    if args.comp:
        pytest_missing = run_and_parse_pytest(test_path, src_path)
        pre_computed_rows = []
        
        for data in analyzed_data:
            try:
                rel_path = str(data["path"].relative_to(current_dir))
                display_name = rel_path
            except ValueError:
                rel_path = None
                display_name = str(data["path"])

            logic_lines_set = set()
            for start, end in data["untested_ranges"]:
                logic_lines_set.update(range(start, end + 1))

            # Szukaj po pełnej ścieżce relatywnej (fix kolizji nazw); fallback na basename
            if rel_path is not None:
                file_missing_in_pytest = pytest_missing.get(rel_path, set())
                if not file_missing_in_pytest:
                    file_missing_in_pytest = pytest_missing.get(data["path"].name, set())
            else:
                file_missing_in_pytest = pytest_missing.get(data["path"].name, set())
            
            # Załaduj AST pliku dla context padding opartego na strukturze kodu.
            # Re-parse jest szybki i izoluje -comp od wewnętrznej reprezentacji process_file.
            try:
                _content = data["path"].read_text(encoding="utf-8", errors="ignore")
                _tree = ast.parse(_content, filename=str(data["path"]))
                parent_ctrl_map = build_parent_ctrl_map(_tree)
            except Exception:
                parent_ctrl_map = {}

            final_missing_logic = set()
            for start, end in data["untested_ranges"]:
                actual_missing_in_range = set(range(start, end + 1)).intersection(file_missing_in_pytest)

                if actual_missing_in_range:
                    final_missing_logic.update(actual_missing_in_range)

                    for line in actual_missing_in_range:
                        # AST-based padding: dodaj nagłówek struktury kontrolnej (if/for/while/try/with).
                        # Zawsze też dodaj line-1 — zapewnia ciągłość zakresu w formacie wyjściowym.
                        # Gdy parent == line-1 (najczęstszy przypadek), set() deduplikuje.
                        # Gdy parent jest dalej, wyjście pokaże "parent, ..., line-1, line" —
                        # semantyczny kontekst bloku + lokalna ciągłość.
                        parent_ctrl = parent_ctrl_map.get(line)
                        if parent_ctrl is not None and parent_ctrl >= start:
                            final_missing_logic.add(parent_ctrl)
                        if line - 1 >= start:
                            final_missing_logic.add(line - 1)
            
            logic_stmts = len(logic_lines_set)
            missing_count = len(final_missing_logic)
            
            if missing_count > logic_stmts:
                logic_stmts = missing_count
                 
            covered_count = logic_stmts - missing_count
            pct = (100 * covered_count / logic_stmts) if logic_stmts > 0 else 100.0
            
            pre_computed_rows.append({
                "name": display_name,
                "stmts": logic_stmts,
                "covered": covered_count,
                "missing": missing_count,
                "pct_val": int(round(pct)),
                "missing_str": format_set_to_ranges(final_missing_logic)
            })

        name_col_width = max(38, max((len(r["name"]) for r in pre_computed_rows), default=38))
        total_header_width = name_col_width + 50
        equal_line = "=" * total_header_width
        dash_line = "-" * total_header_width

        print(f"\n{equal_line}")
        print(f" logic-cov: Logic Coverage Gap Analysis (+branch) ".center(total_header_width, "="))
        print(f"{'Name':<{name_col_width}} {'Logic Stmts':>13} {'Covered':>10} {'Missing':>10} {'Logic Cover%':>13}")
        print(dash_line)

        grand_total_logic = 0
        grand_total_covered = 0
        grand_total_missing = 0

        for r in pre_computed_rows:
            grand_total_logic += r["stmts"]
            grand_total_covered += r["covered"]
            grand_total_missing += r["missing"]
            
            pct_str = f"{r['pct_val']}%"
            print(f"{r['name']:<{name_col_width}} {r['stmts']:>13} {r['covered']:>10} {r['missing']:>10} {pct_str:>13}")
            
            if r["missing_str"]:
                print(f"  ↳ Missing Logic: {r['missing_str']}")

        print(dash_line)
        g_total_pct = (100 * grand_total_covered / grand_total_logic) if grand_total_logic > 0 else 100.0
        print(f"{'TOTAL LOGIC':<{name_col_width}} {grand_total_logic:>13} {grand_total_covered:>10} {grand_total_missing:>10} {f'{int(round(g_total_pct))}%':>13}")
        print(equal_line)
        print(f" target analysis finished ".center(total_header_width, "="))
        print()
        return

    # --- TRYB -v ---
    v_mode = args.v and not args.vv
    if v_mode:
        pre_computed_rows = []
        for data in analyzed_data:
            try:
                display_name = str(data["path"].relative_to(current_dir))
            except ValueError:
                display_name = str(data["path"])
                
            target_pct = (100 * data["logic_lines_count"] / data["total_func_lines"]) if data["total_func_lines"] else 0.0
            
            pre_computed_rows.append({
                "name": display_name,
                "stmts": data["total_func_lines"],
                "logic": data["logic_lines_count"],
                "pct": f"{target_pct:>6.0f}%" if data["total_func_lines"] > 0 else f"{0:>6.0f}%",
                "missing": data["missing_str"] if data["total_func_lines"] > 0 else ""
            })

        name_col_width = max(45, max((len(r["name"]) for r in pre_computed_rows), default=45))
        total_header_width = name_col_width + 50
        equal_line = "=" * total_header_width
        dash_line = "-" * total_header_width

        print(f"\n{equal_line}")
        print(f" logic test-targets: platform analysis ".center(total_header_width, "="))
        print(f"{'Name':<{name_col_width}} {'Stmts':>7} {'Logic':>7} {'Target%':>7}")
        print(dash_line)
        
        grand_total_stmts = 0
        grand_total_logic = 0

        for r in pre_computed_rows:
            grand_total_stmts += r["stmts"]
            grand_total_logic += r["logic"]
            print(f"{r['name']:<{name_col_width}} {r['stmts']:>7} {r['logic']:>7} {r['pct']}")
            # Poprawka formatowania: informacja o brakujących liniach wypisuje się w nowej linii TYLKO gdy faktycznie istnieją
            if r["missing"]:
                print(f"  ↳ Target Logic Lines: {r['missing']}")
                
        print(dash_line)
        total_pct = (100 * grand_total_logic / grand_total_stmts) if grand_total_stmts else 0.0
        print(f"{'TOTAL':<{name_col_width}} {grand_total_stmts:>7} {grand_total_logic:>7} {total_pct:>6.0f}%")
        print(f"{equal_line}")
        print(f" target scanning finished ".center(total_header_width, "="))
        print()
        return

    # --- TRYB -vv ---
    if args.vv:
        print(f"\n================================= verbose function dump ==================================")
        for data in analyzed_data:
            if data["functions_vv"]:
                print(f"\nFILE: {data['path']}")
                print("  " + "-" * 60)
                for line in data["functions_vv"]:
                    print(line)
        print(f"\n================================== end of function dump ==================================\n")

    # --- DOMYŚLNA TABELA PROCENTOWA ---
    table_rows = []
    total_counts = {"GUI": 0, "LOGIC": 0, "MIXED": 0, "UNKNOWN": 0}
    
    for data in analyzed_data:
        f_stats = data["f_stats"]
        for k in total_counts: 
            total_counts[k] += f_stats[k]
            
        f_total = sum(f_stats.values())
        try:
            display_name = str(data["path"].relative_to(current_dir))
        except ValueError:
            display_name = str(data["path"])

        table_rows.append((
            display_name,
            (100 * f_stats["GUI"] / f_total) if f_total else 0.0,
            (100 * f_stats["LOGIC"] / f_total) if f_total else 0.0,
            (100 * f_stats["MIXED"] / f_total) if f_total else 0.0,
            (100 * f_stats["UNKNOWN"] / f_total) if f_total else 0.0
        ))
        
    name_col_width = max(40, max((len(r[0]) for r in table_rows), default=40))
    separator_line = "=" * (name_col_width + 50)
    dash_line = "-" * (name_col_width + 50)

    print(separator_line)
    print(f"{'NAME':<{name_col_width}} | {'GUI':<9} | {'LOGIC':<9} | {'MIXED':<9} | {'UNKNOWN':<9}")
    print(separator_line)
    
    for r in table_rows: 
        print(f"{r[0]:<{name_col_width}} | {r[1]:>8.1f}% | {r[2]:>8.1f}% | {r[3]:>8.1f}% | {r[4]:>8.1f}%")
    
    g_total = sum(total_counts.values())
    if g_total:
        print(dash_line)
        print(f"{'TOTAL':<{name_col_width}} | {(100*total_counts['GUI']/g_total):>8.1f}% | {(100*total_counts['LOGIC']/g_total):>8.1f}% | {(100*total_counts['MIXED']/g_total):>8.1f}% | {(100*total_counts['UNKNOWN']/g_total):>8.1f}%")
        print(separator_line + "\n")
    else:
        print(dash_line)
        print(f"{'TOTAL pusta lub brak funkcji do przeanalizowania.'}")
        print(separator_line + "\n")

if __name__ == "__main__":
    main()
