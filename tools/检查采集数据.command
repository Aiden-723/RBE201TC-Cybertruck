#!/bin/zsh
cd -- "$(dirname "$0")" || exit 1
python3 check_data_interactive.py
