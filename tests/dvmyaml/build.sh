#!/usr/bin/env bash
# Builds yamlcheck against a dvmhost checkout's YAML parser (default /workspace/ref/dvmhost).
set -e
REF=${1:-/workspace/ref/dvmhost}
cd "$(dirname "$0")"
g++ -std=c++17 -O1 -I"$REF/src" -I"$REF/src/common" yamlcheck.cpp "$REF/src/common/yaml/Yaml.cpp" -o yamlcheck
