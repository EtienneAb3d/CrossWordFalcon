#!/usr/bin/env bash
# Builds the Java back end into backend_java/dist/crosswordfalcon-backend.jar.
#
# The jar in dist/ is committed to git, next to dist/sources.sha256 — the
# fingerprint of the src/ tree and pom.xml it was built from — so a fresh
# clone or a `git pull` can run it without rebuilding. Staleness is decided
# by that fingerprint, never by file dates (a checkout gives every file the
# checkout's own date).
#
#   backend_java/build.sh            rebuild only if the sources' fingerprint
#                                    differs from dist/sources.sha256 (or the
#                                    jar is missing)
#   backend_java/build.sh --force    always rebuild
#   backend_java/build.sh --check    exit 0 if the committed jar is up to
#                                    date with the sources, 1 otherwise
#   backend_java/build.sh --print-java
#                                    print the `java` binary to run the jar
#                                    with (used by run_FalconJ.sh) and exit
#
# Building needs a JDK 21+ (javac, not just a runtime) and Maven; running
# the committed jar only needs a Java 21+ runtime. The JDK is looked up, in
# order: $JAVA_HOME, the javac on PATH, /usr/lib/jvm/*, macOS
# JavaVirtualMachines, Homebrew's openjdk@21, then a user-local
# ~/.local/jdk-21* (what Install.sh downloads when no system JDK 21 can be
# installed without root).
set -euo pipefail

cd "$(dirname "$0")"

BUILT_JAR="target/crosswordfalcon-backend.jar"
JAR="dist/crosswordfalcon-backend.jar"
STAMP="dist/sources.sha256"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi \
        | awk '{print $1}'
}

# One hash over every source file's path and content hash, in a fixed
# order, so an edit, an addition, a removal or a rename all change it.
sources_fingerprint() {
    local f
    find src pom.xml -type f | LC_ALL=C sort | while IFS= read -r f; do
        printf '%s %s\n' "$(sha256 < "$f")" "$f"
    done | sha256
}

jar_up_to_date() {
    [ -f "$JAR" ] && [ -f "$STAMP" ] \
        && [ "$(cat "$STAMP")" = "$(sources_fingerprint)" ]
}

java_major() {
    # Major version of the java runtime $1 (0 when absent/unreadable).
    local v
    v=$("$1" -version 2>&1 | awk -F'"' '/version/ {print $2; exit}')
    v="${v#1.}"
    v="${v%%.*}"
    v=$(echo "$v" | tr -cd '0-9')
    echo "${v:-0}"
}

if [ "${1:-}" = "--check" ]; then
    jar_up_to_date
    exit $?
fi

jdk_major() {
    # Major version of the javac in JDK home $1 (0 when absent/unreadable).
    local javac="$1/bin/javac" v
    [ -x "$javac" ] || { echo 0; return; }
    v=$("$javac" -version 2>&1 | awk '{print $2}')
    v="${v#1.}"
    echo "${v%%.*}" | tr -cd '0-9' || echo 0
}

find_jdk() {
    local candidates=() c
    [ -n "${JAVA_HOME:-}" ] && candidates+=("$JAVA_HOME")
    if command -v javac >/dev/null 2>&1; then
        c=$(readlink -f "$(command -v javac)" 2>/dev/null || command -v javac)
        candidates+=("$(dirname "$(dirname "$c")")")
    fi
    for c in /usr/lib/jvm/* /Library/Java/JavaVirtualMachines/*/Contents/Home \
             /opt/homebrew/opt/openjdk@21 /usr/local/opt/openjdk@21 "$HOME"/.local/jdk-21*; do
        [ -d "$c" ] && candidates+=("$c")
    done
    for c in "${candidates[@]}"; do
        if [ "$(jdk_major "$c")" -ge 21 ] 2>/dev/null; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

if [ "${1:-}" = "--print-java" ]; then
    # A JDK's java first, else any Java 21+ runtime on PATH.
    if JDK=$(find_jdk); then
        echo "$JDK/bin/java"
        exit 0
    fi
    if command -v java >/dev/null 2>&1 && [ "$(java_major java)" -ge 21 ]; then
        command -v java
        exit 0
    fi
    echo "Error: no Java 21+ runtime found. Run ./Install.sh, or install one" >&2
    echo "(e.g. 'sudo apt-get install openjdk-21-jre-headless')." >&2
    exit 1
fi

if [ "${1:-}" != "--force" ] && jar_up_to_date; then
    exit 0
fi

if ! JDK=$(find_jdk); then
    echo "Error: the Java back end's sources differ from the committed jar and" >&2
    echo "no JDK 21+ (javac) is available to rebuild it. Run ./Install.sh, or" >&2
    echo "install one (e.g. 'sudo apt-get install openjdk-21-jdk-headless maven')." >&2
    exit 1
fi

MVN="$(command -v mvn || true)"
if [ -z "$MVN" ]; then
    for c in "$HOME"/.local/apache-maven-*/bin/mvn; do
        [ -x "$c" ] && MVN="$c"
    done
fi
if [ -z "$MVN" ]; then
    echo "Error: Maven (mvn) not found. Run ./Install.sh, or install it" >&2
    echo "(e.g. 'sudo apt-get install maven')." >&2
    exit 1
fi

echo "Building the Java back end with $JDK ..."
JAVA_HOME="$JDK" "$MVN" -q package
mkdir -p dist
cp "$BUILT_JAR" "$JAR"
sources_fingerprint > "$STAMP"
echo "Built $(pwd)/$JAR"
