# Release checklist — hermes-share

Drafted 06/11/2026. Nothing here has been done beyond the "built and verified" section, which was
done against upstream `90d6b98f` in a throwaway home.

## Built and verified (done)

- [x] `/share`, `/share confirm`, `/share cancel`, `/share list`, `/unshare` implemented.
- [x] 55 tests green in the plugin's own venv against a temporary `HOME`/`HERMES_HOME` and a real
      `SessionDB`. Run: `uv venv --python 3.14 && uv pip install --python .venv/bin/python pytest
      "ruamel.yaml>=0.18"`, then `PYTHONPATH=<host checkout> .venv/bin/python -m pytest -q`.
- [x] `hermes plugins validate <repo>` — "Validation passed", one `private core imports` warning.
- [x] Live run in a throwaway home on upstream `90d6b98f`: classic CLI preview with planted
      credentials redacted to sentinels, real secret gist created, raw content fetched and grepped
      (none of the planted secrets present), `/unshare last` deleted it, API and web both 404.
- [x] Live TUI run: same preview output, same ledger across surfaces.

## Before the first public release

- [ ] `git init` is local only; create `github.com/Sahil-SS9/hermes-share` and push `main`.
- [ ] Add a CI workflow running the test suite on Python 3.11/3.12/3.13 and `ruff check`.
- [ ] Add `uv.lock` (`uv lock`) and commit it; no exact `==` pins in `pyproject.toml`.
- [ ] Re-run `hermes plugins validate` and `hermes plugins doctor --ci` against a clean checkout of
      the target host, not a personal fork.
- [ ] Confirm the security scan reports no findings (`safe`) at the release commit.
- [ ] Tag `v0.1.0`; the catalog pin must be the release commit, not a branch.
- [ ] Replace `sha:` in `plugin-catalog/hermes-share.yaml` with the full 40-hex SHA.
- [ ] Verify the pin is fetchable: `git ls-remote origin` must show it.

## Catalog submission

- [ ] File the feature issue on the plugin repo first and link it from the PR (`Refs #N`) if the
      reviewer asks for one; the catalog PR is otherwise self-describing.
- [ ] Open one PR adding `plugin-catalog/hermes-share.yaml` to `NousResearch/hermes-agent`, from
      the repository owner. Catalog rule 5: owner or major contributor only.
- [ ] In the PR body, state the private-core-import warning and why there is no alternative seam.
- [ ] Expect a review round on the disclosure paragraph; it must stay exactly true.

## After merge

- [ ] Any later release needs a SHA-bump PR (catalog rule 4) — there is no self-updater, by design.
- [ ] Keep this repository's `main` and the pinned SHA in step; never move a tag.
