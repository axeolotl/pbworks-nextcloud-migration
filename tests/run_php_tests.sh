#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT
git clone --quiet --depth 1 --branch v4.8.0 https://github.com/nextcloud/collectives.git "$temp/collectives"
composer require --working-dir="$temp" --no-interaction --no-progress 'league/commonmark:^2.7'
patch --batch --fuzz=0 -d "$temp/collectives" -p1 < patches/collectives-4.8.patch
php -l "$temp/collectives/lib/Service/ImportService.php"
php tests/importer_test.php "$temp/vendor/autoload.php" "$temp/collectives" \
  "$temp/collectives/lib/Service/ImportService.php"
for file in server/*.php; do php -l "$file"; done
