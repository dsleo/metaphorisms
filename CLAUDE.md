# Metaphorisms

- The list of texts in README.md ("Sources" table) is generated. After any change to `texts/`, `districts/` or the `DATA` blob in `index.html`, run `python3 scripts/build.py` so the README stays in sync, and commit it with the change. `python3 scripts/build.py --check` verifies it.
- The map is additions-only: never move, restyle or remove existing buildings (build.py asserts this). Only genuine metaphors go on the map; texts without one are not added.
