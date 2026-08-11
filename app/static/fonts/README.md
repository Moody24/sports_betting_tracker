# Local web fonts

The UI self-hosts WOFF2 subsets so rendering and visual tests do not depend on
Google Fonts at runtime.

| Family | Weights | Upstream | License |
|---|---|---|---|
| Bricolage Grotesque | 200–800 variable (`opsz` 12–96) | `google/fonts/ofl/bricolagegrotesque` | SIL Open Font License 1.1 |
| JetBrains Mono | 400, 500, 600 | `google/fonts/ofl/jetbrainsmono` | SIL Open Font License 1.1 |

Two families, deliberately. Bricolage Grotesque carries all UI text; its ink
traps and `opsz` axis are the point, so small-size labels set `opsz` low to
let the traps bite. JetBrains Mono carries every number, always with
`tabular-nums`. Syne and Outfit were removed in the Sheet redesign.

The files are unmodified Latin WOFF2 web subsets. Upstream copyright and
license notices remain authoritative; do not replace these binaries with a
different family without updating this inventory.
