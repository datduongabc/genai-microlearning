#!/usr/bin/env bash
# Rebuilds backend/app/video/assets from pinned npm packages.
#
# The slide renderer must work with no network (a worker has no reason to reach a CDN, and a
# CDN outage must not stop a render), so KaTeX and the fonts live in the repo. They are vendored
# rather than installed at image build time because three places need them: the host and CI,
# where the unit tests render slides, and the compose dev container, whose bind mount over /app
# would hide anything the image installed there. Only the woff2 files are kept: Chromium reads
# woff2, and the other formats would triple the size.
set -euo pipefail

KATEX_VERSION=0.16.47
FONTSOURCE_VERSION=5.3.0

ASSETS="$(cd "$(dirname "$0")/.." && pwd)/app/video/assets"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$WORK"
npm pack --silent "katex@${KATEX_VERSION}" "@fontsource/be-vietnam-pro@${FONTSOURCE_VERSION}" \
  "@fontsource/jetbrains-mono@${FONTSOURCE_VERSION}" >/dev/null
for pkg in katex be-vietnam-pro jetbrains-mono; do
  mkdir "$pkg"
  tar xzf ./*"${pkg}"-*.tgz -C "$pkg" --strip-components=1
done

rm -rf "$ASSETS"
mkdir -p "$ASSETS/katex/fonts" "$ASSETS/fonts"

cp katex/dist/katex.min.js "$ASSETS/katex/"
cp katex/dist/fonts/*.woff2 "$ASSETS/katex/fonts/"
# Drop the woff and ttf fallbacks from each @font-face, since only woff2 is vendored.
sed -E 's/,url\(fonts\/[^)]*\.(woff|ttf)\) format\("(woff|truetype)"\)//g' katex/dist/katex.min.css \
  > "$ASSETS/katex/katex.min.css"
cp katex/LICENSE "$ASSETS/katex/LICENSE"

# Be Vietnam Pro 400 and 700 for the slide text, JetBrains Mono 400 for code; the subsets a
# Vietnamese or English slide uses.
fonts_css="$ASSETS/fonts/fonts.css"
: > "$fonts_css"
for spec in be-vietnam-pro:400 be-vietnam-pro:700 jetbrains-mono:400; do
  family="${spec%%:*}"
  weight="${spec##*:}"
  for subset in vietnamese latin-ext latin; do
    cp "$family/files/${family}-${subset}-${weight}-normal.woff2" "$ASSETS/fonts/"
  done
  # Keep the three subsets' @font-face rules, pointing at the copied files, without the woff fallback.
  awk -v RS='\n\n' -v ORS='\n\n' '/-(vietnamese|latin-ext|latin)-[0-9]+-normal \*\//' "$family/${weight}.css" \
    | sed -E -e 's#url\(\./files/#url(#g' -e "s#, url\([^)]*\.woff\) format\('woff'\)##g" >> "$fonts_css"
done
cp be-vietnam-pro/LICENSE "$ASSETS/fonts/LICENSE-be-vietnam-pro"
cp jetbrains-mono/LICENSE "$ASSETS/fonts/LICENSE-jetbrains-mono"

echo "KaTeX ${KATEX_VERSION}, Be Vietnam Pro and JetBrains Mono ${FONTSOURCE_VERSION} -> ${ASSETS}"
