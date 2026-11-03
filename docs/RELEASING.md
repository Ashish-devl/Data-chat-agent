# Releasing

A release publishes four things from one tag:

| What | Where | Install |
| --- | --- | --- |
| Python package | PyPI `datachat-agent` | `pip install datachat-agent` |
| JavaScript SDK | npm `datachat-agent` | `npm install datachat-agent` |
| Server image | `ghcr.io/ashish-devl/datachat-agent` | `docker pull ghcr.io/ashish-devl/datachat-agent` |
| Chat widget | GitHub release asset, and served by every server at `/widget.js` | one script tag |

## One-time setup

PyPI and npm use **trusted publishing**: GitHub Actions proves who it is, so no password or
token is stored in the repository.

**PyPI**
1. Sign in at https://pypi.org (turn on two-factor authentication; PyPI requires it).
2. Account settings → Publishing → *Add a new pending publisher*:
   - PyPI project name: `datachat-agent`
   - Owner: `Ashish-devl` · Repository: `Data-chat-agent`
   - Workflow: `release.yml` · Environment: `pypi`

**npm**
1. Sign in at https://www.npmjs.com (two-factor authentication on; an authenticator app on
   your phone works, no hardware key needed).
2. npm only lets you set up trusted publishing on a package that exists, so the first
   version is published by hand from your computer:
   ```bash
   cd sdk-js && npm login && npm publish --access public
   ```
3. On npmjs.com → the `datachat-agent` package → Settings → Trusted publisher → GitHub Actions:
   - Organization or user: `Ashish-devl` · Repository: `Data-chat-agent`
   - Workflow: `release.yml` · Environment: `npm`

4. Turn npm publishing on: GitHub repo → Settings → Secrets and variables → Actions →
   Variables → New repository variable `PUBLISH_NPM` = `true`. Until then the release skips
   npm and publishes everything else.

**GitHub**: Settings → Environments → create `pypi` and `npm` (add yourself as a required
reviewer if you want to approve each release). The Docker image needs no setup.

## Each release

1. Set the same version in `pyproject.toml` and `sdk-js/package.json` (e.g. `0.2.0`).
2. Commit, then tag and push:
   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```
3. Watch the **Release** workflow on GitHub. Versions starting `0.` and tags with a `-`
   (e.g. `v1.0.0-rc.1`) are marked as pre-releases.
