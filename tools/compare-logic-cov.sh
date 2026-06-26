#!/bin/bash
# =============================================================================
# Skrypt do porównywania plików repo <-> system
# =============================================================================

# --- KONFIGURACJA: Tutaj definiujesz swoje pliki ---
# Format: "ścieżka/w/repo:ścieżka/w/systemie"
FILES_TO_CHECK=(
    "../logic-cov.py:$HOME/.local/bin/logic-cov"
#    "../m_weather.py:$HOME/.local/bin/m_weather.py"
#    "utils.py:$HOME/.local/bin/GlavaMP/utils.py"
)

# --- Kolory ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
GRAY='\033[0;37m'
BOLD='\033[1m'
NC='\033[0m'

# Liczniki (zwykłe zmienne, brak subshell w pętli for = brak plików tymczasowych!)
OK_N=0
DIFF_N=0
MISSING_N=0

echo -e "${BOLD}=== Porównanie plików ===${NC}\n"

for entry in "${FILES_TO_CHECK[@]}"; do
    # Rozdzielenie pary po dwukropku
    repo_file="${entry%%:*}"
    inst_file="${entry#*:}"
    label=$(basename "$repo_file")

    # 1. Czy plik w ogóle istnieje w repozytorium? (Zabezpieczenie)
    if [ ! -f "$repo_file" ]; then
        echo -e " ${GRAY}[?] Pominięto:${NC} $label (brak pliku źródłowego w repo)"
        continue
    fi

    # 2. Czy plik istnieje w systemie?
    if [ ! -f "$inst_file" ]; then
        echo -e " ${RED}✗ BRAK:${NC} ${BOLD}$label${NC}"
        echo -e "   ${GRAY}Oczekiwano w: $inst_file${NC}"
        echo -e "   ${GRAY}Komenda instalacji:${NC} cp \"$repo_file\" \"$inst_file\""
        ((MISSING_N++))
        continue
    fi

    # 3. Porównanie zawartości
    if ! diff -q "$repo_file" "$inst_file" > /dev/null 2>&1; then
        echo -e " ${YELLOW}≠ RÓŻNI SIĘ:${NC} ${BOLD}$label${NC}"
        echo -e "   ${GRAY}Aktualizuj repo:  ${NC} cp \"$inst_file\" \"$repo_file\""
        echo -e "   ${GRAY}Aktualizuj system:${NC} cp \"$repo_file\" \"$inst_file\""
        ((DIFF_N++))
    else
        echo -e " ${GREEN}✓ OK:${NC} $label"
        ((OK_N++))
    fi
done

# --- Podsumowanie ---
TOTAL=$((OK_N + DIFF_N + MISSING_N))
echo -e "\n${BOLD}=== Podsumowanie ===${NC}"
echo -e "Sprawdzono plików: $TOTAL"
echo -e " ${GREEN}✓ Zgodne:     $OK_N${NC}"
echo -e " ${YELLOW}≠ Różne:      $DIFF_N${NC}"
echo -e " ${RED}✗ Brakujące:  $MISSING_N${NC}"

# Zwracamy kod błędu, jeśli coś jest nie tak (przydatne do CI/CD albo skryptów automatycznych)
[ $((DIFF_N + MISSING_N)) -eq 0 ] && exit 0 || exit 1
