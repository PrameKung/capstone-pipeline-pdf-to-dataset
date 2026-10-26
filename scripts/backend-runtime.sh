#!/bin/sh
# Supply native wheel dependencies from the active NixOS compiler, offline.
set -eu

if [ -f /etc/NIXOS ]; then
    if command -v gcc >/dev/null 2>&1; then
        pipeline_cpp_library=$(gcc -print-file-name=libstdc++.so.6)
        if [ -f "$pipeline_cpp_library" ]; then
            pipeline_cpp_directory=$(dirname "$pipeline_cpp_library")
            export LD_LIBRARY_PATH="$pipeline_cpp_directory${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        else
            echo 'Cannot locate libstdc++.so.6 using gcc; provide its directory in LD_LIBRARY_PATH.' >&2
            exit 1
        fi
    elif [ -z "${LD_LIBRARY_PATH:-}" ]; then
        echo 'NixOS native PDF dependencies need gcc on PATH or libstdc++.so.6 in LD_LIBRARY_PATH.' >&2
        exit 1
    fi
fi

exec "$@"
