#!/usr/bin/env python3

import ast
import sys
from pathlib import Path

# --- Słowa kluczowe ---
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

            if (
                short in GUI_HINTS
                or short in GUI_WIDGETS
                or any(p in ["tk", "ttk", "theme", "style"] for p in parts)
            ):
                self.gui += 1

            if (
                short in LOGIC_HINTS 
                or any(x in name for x in LOGIC_HINTS)
                or any(p in ["os", "sys", "subprocess", "json", "pathlib", "re"] for p in parts)
            ):
                self.logic += 1

        self.generic_visit(node)

    def _name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._name(node.value)
            if base:
                return f"{base}.{node.attr}"
            return node.attr
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


def analyze_file_for_coverage(path):
    try:
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        return None

    total_func_lines = 0
    logic_lines_count = 0
    untested_ranges = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind, _, _ = analyze_function(node)
            
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", start_line)
            lines_in_func = (end_line - start_line) + 1
            
            total_func_lines += lines_in_func
            
            if kind in ("LOGIC", "MIXED"):
                logic_lines_count += lines_in_func
                untested_ranges.append((start_line, end_line))

    untested_ranges.sort()
    range_strings = []
    for start, end in untested_ranges:
        if start == end:
            range_strings.append(f"{start}")
        else:
            range_strings.append(f"{start}-{end}")
    
    missing_str = ", ".join(range_strings)

    return {
        "total_lines": total_func_lines,
        "logic_lines": logic_lines_count,
        "missing_ranges": missing_str
    }


def analyze_file_vv(path):
    try:
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        return []

    functions_output = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind, gui, logic = analyze_function(node)
            functions_output.append(
                f"    {kind:7} | gui={gui:<3} logic={logic:<3} | {node.name}"
            )
    return functions_output


def main():
    # Pobieranie flag na wzór pytest
    vv_mode = "-vv" in sys.argv
    v_mode = "-v" in sys.argv and not vv_mode

    scripts_dir = Path("scripts")
    if not scripts_dir.exists():
        print("Błąd: Folder 'scripts' nie istnieje.")
        sys.exit(1)

    files = sorted(list(scripts_dir.glob("**/*.py")))
    if not files:
        print("Nie znaleziono plików .py.")
        sys.exit(1)

    # -------------------------------------------------------------
    # OPCJA 1: FLAGA -v (Raport pokrycia / pytest-cov)
    # -------------------------------------------------------------
    if v_mode:
        print(f"\n---------- logic test-targets: platform analysis -----------")
        print(f"{'Name':<45} {'Stmts':>7} {'Logic':>7} {'Target%':>7}  {'Untested Logic Lines'}")
        print("-" * 95)
        
        grand_total_stmts = 0
        grand_total_logic = 0

        for path in files:
            data = analyze_file_for_coverage(path)
            if not data: continue
                
            grand_total_stmts += data["total_lines"]
            grand_total_logic += data["logic_lines"]
            target_pct = (100 * data["logic_lines"] / data["total_lines"]) if data["total_lines"] else 0.0
            
            if data["total_lines"] > 0:
                print(f"{str(path):<45} {data['total_lines']:>7} {data['logic_lines']:>7} {target_pct:>6.0f}%  {data['missing_ranges']}")
            else:
                print(f"{str(path):<45} {0:>7} {0:>7}   {0:>3}%")
                
        print("-" * 95)
        total_pct = (100 * grand_total_logic / grand_total_stmts) if grand_total_stmts else 0.0
        print(f"{'TOTAL':<45} {grand_total_stmts:>7} {grand_total_logic:>7} {total_pct:>6.0f}%")
        print(f"\n==================== target scanning finished ====================\n")
        return

    # -------------------------------------------------------------
    # OPCJA 2: FLAGA -vv (Szczegółowy zrzut per funkcja)
    # -------------------------------------------------------------
    if vv_mode:
        print(f"\n==================== verbose function dump ====================")
        for path in files:
            lines = analyze_file_vv(path)
            if lines:
                print(f"\nFILE: {path}")
                print("  " + "-" * 60)
                for line in lines:
                    print(line)
        print(f"\n==================== end of function dump ====================\n")

    # -------------------------------------------------------------
    # DOMYŚLNA TABELA PROCENTOWA (Dla braku flag oraz na końcu -vv)
    # -------------------------------------------------------------
    table_rows = []
    total_counts = {"GUI": 0, "LOGIC": 0, "MIXED": 0, "UNKNOWN": 0}
    
    for path in files:
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except:
            continue
        f_stats = {"GUI": 0, "LOGIC": 0, "MIXED": 0, "UNKNOWN": 0}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind, _, _ = analyze_function(node)
                f_stats[kind] += max(1, len(node.body))
                
        for k in total_counts: 
            total_counts[k] += f_stats[k]
            
        f_total = sum(f_stats.values())
        table_rows.append((
            str(path),
            (100 * f_stats["GUI"] / f_total) if f_total else 0.0,
            (100 * f_stats["LOGIC"] / f_total) if f_total else 0.0,
            (100 * f_stats["MIXED"] / f_total) if f_total else 0.0,
            (100 * f_stats["UNKNOWN"] / f_total) if f_total else 0.0
        ))
        
    print(f"{'=' * 95}\n{'NAZWA PLIKU':<40} | {'GUI':<10} | {'LOGIC':<10} | {'MIXED':<10} | {'UNKNOWN':<10}\n{'=' * 95}")
    for r in table_rows: 
        print(f"{r[0]:<40} | {r[1]:>8.1f}% | {r[2]:>8.1f}% | {r[3]:>8.1f}% | {r[4]:>8.1f}%")
    g_total = sum(total_counts.values())
    print(f"{'-' * 95}\n{'SUMA GLOBALNA':<40} | {(100*total_counts['GUI']/g_total):>8.1f}% | {(100*total_counts['LOGIC']/g_total):>8.1f}% | {(100*total_counts['MIXED']/g_total):>8.1f}% | {(100*total_counts['UNKNOWN']/g_total):>8.1f}%\n{'=' * 95}\n")


if __name__ == "__main__":
    main()
