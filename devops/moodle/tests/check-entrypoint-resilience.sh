#!/bin/bash
# The entrypoint must reach its command even when the course category setup fails.
# Runs anywhere: php and chown are replaced by stubs, so no Moodle is needed.
set -u

entrypoint="$(cd "$(dirname "$0")/.." && pwd)/docker-entrypoint.sh"
stubs="$(mktemp -d)"
trap 'rm -rf "$stubs"' EXIT

cat > "$stubs/php" <<'STUB'
#!/bin/bash
case "$1" in
    */configure-course-categories.php) echo "category setup failed" >&2; exit 1 ;;
esac
exit 0
STUB
printf '#!/bin/bash\nexit 0\n' > "$stubs/chown"
chmod +x "$stubs/php" "$stubs/chown"

output="$(PATH="$stubs:$PATH" bash "$entrypoint" echo apache-started 2>&1)"
status=$?
if [ $status -ne 0 ] || [ "$(tail -n 1 <<< "$output")" != "apache-started" ]; then
    echo "Entrypoint did not reach its command (exit $status):" >&2
    echo "$output" >&2
    exit 1
fi
echo "Entrypoint resilience check passed"
