#!/bin/bash
# Запуск reader + curator. Reader сначала, curator после.
cd $HOME/health_scripts
/opt/homebrew/bin/python3.11 publication_reader.py --max 10 2>&1
/opt/homebrew/bin/python3.11 literature_curator.py --max 10 2>&1
