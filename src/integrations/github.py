import asyncio

import httpx

from src.graph.state import ReviewRequest
from src.utils.config import settings


class GitHubClient:
    """Minimal async GitHub REST client for PR review workflow.

    Uses two endpoints to assemble a `ReviewRequest`:
      - GET /repos/{owner}/{repo}/pulls/{number}        → metadata (author, etc.)
      - GET /repos/{owner}/{repo}/pulls/{number}/files  → file list + per-file patch

    A unified diff is reconstructed from per-file `patch` fields.
    """

    def __init__(self):
        self.base_url = settings.GITHUB_API_URL.rstrip("/")
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # Anonymous access is fine for public repos (60 req/h per IP); skip
        # the Authorization header rather than send `Bearer ""` which GitHub
        # treats as an invalid token (401).
        if settings.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_pr(self, pr_url: str) -> ReviewRequest:
        owner_repo, pr_number = self._parse_pr_url(pr_url)
        api_base = f"/repos/{owner_repo}/pulls/{pr_number}"

        pr_response, files_response = await asyncio.gather(
            self._client.get(api_base),
            self._client.get(f"{api_base}/files"),
        )
        pr_response.raise_for_status()
        files_response.raise_for_status()

        pr_data = pr_response.json()
        files_data = files_response.json()

        files_changed = [f["filename"] for f in files_data]
        raw_diff = "\n".join(
            f"diff --git a/{f['filename']} b/{f['filename']}\n{f.get('patch', '')}"
            for f in files_data
            if f.get("patch")
        )

        return ReviewRequest(
            pr_url=pr_url,
            diff=raw_diff,
            files_changed=files_changed,
            author=pr_data["user"]["login"],
        )

    async def post_pr_comment(self, pr_url: str, body: str) -> int:
        """Post a general issue-style comment to the PR conversation.

        GitHub treats PR conversation comments as issue comments at the API level,
        which is the right primitive for a top-level review verdict. Inline review
        comments are a separate endpoint not used here.
        """
        owner_repo, pr_number = self._parse_pr_url(pr_url)
        response = await self._client.post(
            f"/repos/{owner_repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        response.raise_for_status()
        return response.json()["id"]

    def _parse_pr_url(self, url: str) -> tuple[str, str]:
        """
        Parses URLs like:
          https://github.com/owner/repo/pull/42
          https://github.com/owner/repo/pull/42/files
          https://github.enterprise.com/owner/repo/pull/42
        Returns ("owner/repo", pr_number).
        """
        parts = url.rstrip("/").split("/")
        if "pull" not in parts:
            raise ValueError(f"Not a valid GitHub PR URL (missing 'pull' segment): {url!r}")
        pull_index = parts.index("pull")
        if pull_index < 4 or len(parts) <= pull_index + 1:
            raise ValueError(f"Not a valid GitHub PR URL: {url!r}")
        pr_number = parts[pull_index + 1]
        if not pr_number.isdigit():
            raise ValueError(f"PR number must be numeric, got {pr_number!r} in {url!r}")
        owner = parts[pull_index - 2]
        repo = parts[pull_index - 1]
        return f"{owner}/{repo}", pr_number
