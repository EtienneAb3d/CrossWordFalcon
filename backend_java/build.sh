#!/usr/bin/env bash
# Builds the Java back end (backend_java/target/crosswordfalcon-backend.jar).
#
#   backend_java/build.sh            rebuild only if a source file or pom.xml
#                                    is newer than the jar (or the jar is missing)
#   backend_java/build.sh --force    always rebuild
#   backend_java/build.sh --print-java
#                                    print the `java` binary of the JDK found
#                                    (used by run_FalconJ.sh) and exit
#
# Needs a JDK 21+ (javac, not just a runtime) and Maven. The JDK is looked
# up, in order: $JAVA_HOME, the javac on PATH, /usr/lib/jvm/*, macOS
# JavaVirtualMachines, Homebrew's openjdk@21, then a user-local
# ~/.local/jdk-21* (what Install.sh downloads when no system JDK 21 can be
# installed without root).
set -euo pipefail

cd "$(dirname "$0")"

JAR="target/crosswordfalcon-backend.jar"

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

if ! JDK=$(find_jdk); then
    echo "Error: no JDK 21+ found (javac). Run ./Install.sh, or install one" >&2
    echo "(e.g. 'sudo apt-get install openjdk-21-jdk-headless maven')." >&2
    exit 1
fi

if [ "${1:-}" = "--print-java" ]; then
    echo "$JDK/bin/java"
    exit 0
fi

if [ "${1:-}" != "--force" ] && [ -f "$JAR" ] \
   && [ -z "$(find src pom.xml -newer "$JAR" -print -quit 2>/dev/null)" ]; then
    exit 0
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
echo "Built $(pwd)/$JAR"
