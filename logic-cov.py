#!/usr/bin/env python3

import ast
import sys
import argparse
from pathlib import Path
import subprocess
import re

# --- Metric models ---
METRIC_LINE = "line"
METRIC_STRUCT = "struct"

# --- Key words ---
GUI_HINTS = {
    "pack", "grid", "place", "bind", "config", "configure", 
    "StringVar", "BooleanVar", "IntVar", "DoubleVar", "PhotoImage",
    "mainloop", "Tk", "title", "geometry", "update", "after", "destroy",
    "focus_set", "window", "menu", "style", "theme"
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

class Analyzer(ast.NodeVisitor):
    def __init__(self, func_name=""):
        self.gui = 0
        self.logic = 0
        
        func_name_lower = func_name.lower()
        if any(hint in func_name_lower for hint in GUI_NAME_HINTS):
            self.gui += 2
        if any(hint in func_name_lower for hint in LOGIC_NAME_HINTS):
            self.logic += 2

    def visit_Call(self, node):
        name = self._name(node.func)
        if name:
            parts = name.split(".")
            short = parts[-1]

            # GUI verification
            if (
                short in GUI_HINTS
                or short in GUI_WIDGETS
                or any(p.lower() in ["tk", "ttk", "theme", "style"] for p in parts)
            ):
                self.gui += 1

            # LOGIC verification (precise segment matching instead of substrings)
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
            # Handling call chains, e.g., get_widget().pack() -> extracting the trailing dot
            return self._name(node.func)
        return None


def analyze_function(node):
    a = Analyzer(func_name=node.name)
    a.visit(node)

    gui = a.gui
    logic = a.logic
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


def process_file(path):
    """Parses the file only ONCE and gathers all required statistics"""
    try:
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        return None

    file_results = {
        "path": path,
        "total_func_lines": 0,
        "logic_lines_count": 0,
        "untested_ranges": [],
        "functions_vv": [],
        "f_stats": {"GUI": 0, "LOGIC": 0, "MIXED": 0, "UNKNOWN": 0}
    }

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind, gui, logic = analyze_function(node)
            
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

    # Line range formatting
    file_results["untested_ranges"].sort()
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
        description="GUI/LOGIC analyzer for Python projects"
    )
    # DEFINIUJEMY NAJPIERW TESTY, POTEM ŹRÓDŁA:
    parser.add_argument(
        "test_path", nargs="?", default="tests",
        help="Directory containing pytest tests (default: 'tests')"
    )
    parser.add_argument(
        "src_path", nargs="?", default=".",
        help="Directory containing application source code (default: current directory)"
    )
    
    parser.add_argument("-v", action="store_true", help="Show logic coverage targets")
    parser.add_argument("-vv", action="store_true", help="Show function classification dump")
    parser.add_argument("-comp", action="store_true", help="Compare static targets with live pytest-cov results")
    return parser.parse_args()


def collect_files(paths):
    files = set()
    for item in paths:
        path = Path(item)
        if path.is_file() and path.suffix == ".py":
            files.add(path.resolve())
        elif path.is_dir():
            for f in path.rglob("*.py"):
                files.add(f.resolve())
    return sorted(files)


# --- NOWE FUNKCJE POMOCNICZE DLA TRYBU -comp ---

def parse_line_ranges(range_str):
    """Konwertuje string zakresów pytest typu '40-45, 50' na zestaw liczb (set)"""
    lines = set()
    if not range_str.strip():
        return lines
    for part in range_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = map(int, part.split("-"))
            lines.update(range(start, end + 1))
        else:
            lines.add(int(part))
    return lines


def format_set_to_ranges(line_set):
    """Konwertuje set numerów linii z powrotem na czytelne zakresy tekstowe"""
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
    """Uruchamia pytest w pamięci i parsuje brakujące linie z term-missing"""
    # Zastępujemy "pytest" dynamicznym wywołaniem aktualnego interpretera z flagą -m pytest
    cmd = [
        sys.executable, "-m", "pytest", 
        str(test_path), 
        "-v", 
        f"--cov={src_path}", 
        "--cov-report=term-missing"
    ]
    try:
        # Dodajemy env=os.environ, aby przekazać zmienne środowiskowe, np. PYTHONPATH
        import os
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, env=os.environ)
        stdout = result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Dodatkowa korzyść: wypisujemy prawdziwy błąd (stderr), żeby wiedzieć, co poszło nie tak
        stderr_output = getattr(e, "stderr", "")
        print(f"Error: Failed to run pytest command: {' '.join(cmd)}")
        if stderr_output:
            print(f"Pytest Output:\n{stderr_output}")
        sys.exit(1)
    
    # ... reszta kodu parsowania bez zmian ...

    coverage_data = {}
    for line in stdout.splitlines():
        if not line.strip() or line.startswith("---") or line.startswith("Name"):
            continue
        if ".py" not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        # Wiersz tabelki coverage MUSI mieć stmts/miss/cover% jako liczby
        # na pozycjach 1-3. Jeśli nie — to szum z verbose pytest (np.
        # nodeid z parametryzacją zawierającą spacje, "PASSED [ XX%]"),
        # nie wiersz coverage. Ignorujemy bez crashowania.
        try:
            int(parts[1])
            int(parts[2])
        except ValueError:
            continue

        pure_name = Path(parts[0]).name
        if len(parts) > 4:
            missing_str = " ".join(parts[4:])
            coverage_data[pure_name] = parse_line_ranges(missing_str)
        else:
            coverage_data[pure_name] = set()
    return coverage_data

def main():
    args = parse_args()
    
    # Przejrzyste przypisanie z argparse
    test_path = args.test_path
    src_path = args.src_path

    # Jeśli działamy w trybie -comp, analizujemy statycznie TYLKO kod źródłowy
    if args.comp:
        files = collect_files([src_path])
    else:
        # W standardowym trybie analizujemy oba podane foldery
        files = collect_files([src_path, test_path])

    # ... reszta kodu bez zmian ...

    if not files:
        print("No .py files found in the specified directory.")
        sys.exit(1)

    # Analiza AST
    analyzed_data = []
    for path in files:
        data = process_file(path)
        if data:
            analyzed_data.append(data)

    current_dir = Path.cwd()

    # ---------------------------------------------------------------
    # NOWY BLOK REPREZENTACJI: TRYB -comp 
    # ---------------------------------------------------------------
    if args.comp:
        pytest_missing = run_and_parse_pytest(test_path, src_path)
        pre_computed_rows = []
        
        for data in analyzed_data:
            pure_name = data["path"].name
            try:
                display_name = str(data["path"].relative_to(current_dir))
            except ValueError:
                display_name = str(data["path"])
                
            # Wyciągamy zestaw wszystkich linii zakwalifikowanych jako LOGIC lub MIXED
            logic_lines_set = set()
            for start, end in data["untested_ranges"]:
                logic_lines_set.update(range(start, end + 1))
                
            # Porównujemy z liniami, które pytest zaraportował jako niepokryte
            file_missing_in_pytest = pytest_missing.get(pure_name, set())
            
            # --- NOWY BLOK: INTELIGENTNE UZUPEŁNIANIE KONTEKSTU (np. IF) ---
            final_missing_logic = set()
            for start, end in data["untested_ranges"]:
                # Szukamy linii z tego konkretnego zakresu, których brakuje w pytest
                actual_missing_in_range = set(range(start, end + 1)).intersection(file_missing_in_pytest)
                
                if actual_missing_in_range:
                    final_missing_logic.update(actual_missing_in_range)
                    
                    # Sprawdzamy każdą brakującą linię i cofamy się o 1-2 linie, 
                    # aby dołączyć instrukcje sterujące (if, for, try) z tej samej funkcji
                    for line in actual_missing_in_range:
                        for offset in (1, 2):
                            parent_line = line - offset
                            if parent_line >= start:
                                final_missing_logic.add(parent_line)
            # ---------------------------------------------------------------
            
            logic_stmts = len(logic_lines_set)
            missing_count = len(final_missing_logic)
            
            # Zapobiegamy sytuacji, w której przez dodanie kontekstu 
            # liczba braków przewyższyłaby całkowitą liczbę linii logiki
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
        print(f" logic-cov: Logic Coverage Gap Analysis ".center(total_header_width, "="))
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

    # --- OPTION 1: -v FLAG (Coverage report / pytest-cov) ---
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
        print(f"{'Name':<{name_col_width}} {'Stmts':>7} {'Logic':>7} {'Target%':>7}  {'Logic Lines'}")
        print(dash_line)
        
        grand_total_stmts = 0
        grand_total_logic = 0

        for r in pre_computed_rows:
            grand_total_stmts += r["stmts"]
            grand_total_logic += r["logic"]
            print(f"{r['name']:<{name_col_width}} {r['stmts']:>7} {r['logic']:>7} {r['pct']}  {r['missing']}")
                
        print(dash_line)
        total_pct = (100 * grand_total_logic / grand_total_stmts) if grand_total_stmts else 0.0
        print(f"{'TOTAL':<{name_col_width}} {grand_total_stmts:>7} {grand_total_logic:>7} {total_pct:>6.0f}%")
        print(f"{equal_line}")
        print(f" target scanning finished ".center(total_header_width, "="))
        print()
        return

    # --- OPTION 2: -vv FLAG (Detailed per-function output) ---
    if args.vv:
        print(f"\n==================================== verbose function dump ====================================")
        for data in analyzed_data:
            if data["functions_vv"]:
                print(f"\nFILE: {data['path']}")
                print("  " + "-" * 60)
                for line in data["functions_vv"]:
                    print(line)
        print(f"\n===================================== end of function dump ====================================\n")

    # -------------------------------------------------------------
    # DEFAULT PERCENTAGE TABLE (Zabezpieczona przed rozjeżdżaniem)
    # -------------------------------------------------------------
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
    separator_line = "=" * (name_col_width + 55)
    dash_line = "-" * (name_col_width + 55)

    print(separator_line)
    print(f"{'NAME':<{name_col_width}} | {'GUI':<10} | {'LOGIC':<10} | {'MIXED':<10} | {'UNKNOWN':<10}")
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
