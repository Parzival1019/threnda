#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
packaging_dir="$repo_root/packaging/macos"
previous_build=$(mktemp -d "${TMPDIR:-/tmp}/threnda-desktop-build.XXXXXX")

if [ -e "$repo_root/build/python3.12-standalone" ]; then
    mv "$repo_root/build/python3.12-standalone" "$previous_build/"
fi
if [ -e "$repo_root/dist/Threnda.app" ]; then
    mv "$repo_root/dist/Threnda.app" "$previous_build/"
fi

cd "$packaging_dir"

python setup.py -q py2app --dist-dir "$repo_root/dist" --bdist-base "$repo_root/build"

printf '%s\n' "Built: $repo_root/dist/Threnda.app"
