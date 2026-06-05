from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.graph.state import ALLOWED_SCOPE_ROLES

# `GITHUB_TOKEN` is optional: public repos can be fetched anonymously
# (rate-limited to 60 req/h per IP). When empty the GitHub client omits the
# Authorization header instead of sending `Bearer ""`.
REQUIRED_SETTINGS = ("ANTHROPIC_API_KEY",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # AI Gateway — any OpenAI-compatible endpoint. The default assumes a
    # local serving box on `localhost:8001`. Real deployments override
    # via the gitignored `.env` (e.g. a corp-LAN gateway, Bifrost /
    # LiteLLM proxy, or a public provider). The class default stays
    # sanitised so this file can live in a public repository.
    USE_AI_GATEWAY: bool = True
    AI_GATEWAY_URL: str = "http://localhost:8001/v1"
    AI_GATEWAY_API_KEY: str = ""
    # Pin a specific model name on the gateway. When empty, ModelFactory
    # auto-discovers by GETing /v1/models and using the first id; failing
    # that, it falls back to the Bifrost-style `provider/model` mapping.
    AI_GATEWAY_MODEL: str = ""

    # Anthropic (required only when USE_AI_GATEWAY=False)
    ANTHROPIC_API_KEY: str = ""

    # OpenAI (optional)
    OPENAI_API_KEY: str = ""

    # Google (optional)
    GOOGLE_API_KEY: str = ""

    # GitHub (required)
    GITHUB_TOKEN: str = ""
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_WEBHOOK_SECRET: str = ""
    # SSRF guard — allowlist of hosts `clone_repo` / `normalize_repo_url`
    # will accept. Anything else is rejected with `ValueError` before any
    # subprocess fires; this also stops `_inject_token` from leaking the
    # GitHub PAT as basic-auth to a third-party server.
    #
    # CSV in env: GITHUB_ALLOWED_HOSTS=github.com,ghe.corp.example
    # Default = github.com. On GitHub Enterprise add your host here.
    GITHUB_ALLOWED_HOSTS: list[str] = ["github.com"]

    # GitHub MCP sidecar (read-only, optional). When enabled, reviewers receive a
    # repo-context block (description, file tree, README excerpt) alongside the diff.
    ENABLE_GITHUB_MCP: bool = False
    GITHUB_MCP_URL: str = "http://localhost:3003/mcp"

    # Postgres — backs LangGraph checkpointer AND pgvector vectorstore.
    # `localhost` is the default for running the app from .venv against the
    # compose-managed db; docker-compose overrides the host to `postgres`.
    DATABASE_URL: str = "postgresql://ia:ia@localhost:5432/ia_reviewer"

    # How long to cache reviewer reference materials before re-fetching
    CONTEXT_TTL_HOURS: int = 24

    # Validator: scores 0..10 (10 = totally inappropriate). >= threshold → rejected.
    VALIDATION_REJECT_THRESHOLD: int = 7

    # Reviewer roles to skip when the PR diff touches only documentation files.
    # CSV in env (e.g. "security,architecture"); empty list = never skip.
    SKIP_REVIEW_FOR_DOCS_ONLY: list[str] = []

    # Repo-mode review: hard cap on files one reviewer processes per run
    # (one LLM call per file). Files beyond the cap are skipped with an
    # explicit "truncated: N" note in the final report. 200 covers most
    # real-world repos while keeping cost predictable.
    MAX_FILES_PER_AGENT: int = 200

    # Repo-mode review: total-tree size cap enforced by the validator. Trees
    # larger than this are rejected outright as `oversized_repo` — we won't
    # even start the per-specialist passes. Separate from MAX_FILES_PER_AGENT,
    # which is per reviewer after whitelist filtering.
    MAX_REPO_FILES_HARD: int = 5000

    # Directory (relative to cwd or absolute) where repo-mode reports are
    # written and the `/reports/{thread_id}.md` endpoint reads from.
    REPORTS_DIR: str = "reports"

    # Directory where `git clone --depth=1` snapshots land (one tempdir per
    # review, removed in `finally:` once the graph finishes). Lives inside
    # the project rather than `$TMPDIR` so it's predictable under Docker
    # (where /tmp is ephemeral) and easy to bind-mount via a volume.
    SNAPSHOTS_DIR: str = "snapshots"

    @field_validator("SKIP_REVIEW_FOR_DOCS_ONLY", mode="before")
    @classmethod
    def _split_skip_csv(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("GITHUB_ALLOWED_HOSTS", mode="before")
    @classmethod
    def _split_allowed_hosts_csv(cls, v):
        """`GITHUB_ALLOWED_HOSTS=github.com,ghe.corp.example` → list. Lower-
        case here so host comparison can be case-insensitive without each
        caller having to remember to `.lower()`.
        """
        if isinstance(v, str):
            return [s.strip().lower() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip().lower() for s in v if s and str(s).strip()]
        return v

    @field_validator("WS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_ws_origins_csv(cls, v):
        """`WS_ALLOWED_ORIGINS=http://a,http://b` → list. Lower-case the
        scheme + host (origin comparison is case-insensitive per spec).
        Empty / blank values yield `[]` which means "no Origin check".
        """
        if isinstance(v, str):
            return [s.strip().lower() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip().lower() for s in v if s and str(s).strip()]
        return v

    # FIFO cap on the number of threads `ChatStore` + `ProgressStore`
    # retain in memory. 0 (or unset) disables the cap. Each thread holds
    # at most a few hundred messages/events, so 256 threads ≈ a couple
    # of MB — plenty of headroom while avoiding the unbounded-growth
    # foot-gun.
    IN_MEMORY_STORE_MAX_THREADS: int = 256

    # WebSocket Origin allowlist — browsers send `Origin` on every WS
    # handshake. Refusing unknown origins blocks CSRF-style attacks where
    # a malicious site loaded in the user's browser would otherwise open
    # a WS to our localhost server and drive the exploit-approval flow.
    #
    # CSV in env: WS_ALLOWED_ORIGINS=http://localhost:8000,https://reviews.corp.example
    # Empty list (`WS_ALLOWED_ORIGINS=`) disables the check entirely —
    # escape hatch for ops who can't enumerate every dev's localhost.
    # Missing Origin header (non-browser clients) is always allowed.
    WS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # ── RAG: retrieval over past findings (optional, fail-soft) ──────────
    # EMBEDDING_MODEL is the master switch: empty = RAG disabled, no
    # EmbeddingsClient is built, no retrieval is attempted. When set, the
    # app expects the gateway to expose /embeddings serving this model.
    EMBEDDING_MODEL: str = ""
    # Dim of the pgvector column `review_findings.embedding`. Changing
    # this requires recreating the column + index — do NOT flip on a
    # live database. 1024 is the BGE-large / multilingual-e5-large default.
    EMBEDDING_DIM: int = 1024
    # Where the embeddings endpoint lives. Empty = reuse AI_GATEWAY_URL.
    # Different value useful when chat goes to one box and embeddings to
    # a dedicated embeddings server.
    EMBEDDING_API_URL: str = ""
    # Auth for the embeddings endpoint. Empty = reuse AI_GATEWAY_API_KEY.
    EMBEDDING_API_KEY: str = ""
    # How many past findings per role to splice into a reviewer's prompt.
    # Low number on purpose — quality of recall matters more than quantity,
    # and the prompt bloat from a long list hurts more than it helps.
    RAG_TOP_K: int = 5

    # ── LLM-as-judge model override ─────────────────────────────────────
    # The judge (`src/evals/llm_judge.py`) defaults to the same model as
    # the reviewers. In practice you often want it on a **stronger** model
    # than the reviewer — the judge runs once per report, so the higher
    # per-call cost is acceptable, and a smarter judge catches a
    # weaker reviewer's hallucinations.
    #
    # Empty (default) = use LLMJudge's built-in default
    # (`claude-sonnet-4-6`). Set to any internal model alias supported by
    # `ModelFactory` (e.g. `claude-opus-4-7`, `gpt-4o`) or directly to a
    # gateway-served model id when the gateway requires it.
    JUDGE_MODEL: str = ""

    # Langfuse observability (optional — both keys must be set to enable)
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    # Server-to-server URL the Python client POSTs traces to. In docker-compose
    # this resolves to the in-network hostname `http://langfuse-web:3000`;
    # for Langfuse Cloud, set `https://cloud.langfuse.com`.
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    # Browser-facing URL the UI's "View traces ↗" link points at. Usually
    # different from LANGFUSE_HOST when running self-hosted via docker-compose
    # (the browser can't resolve docker-internal hostnames). Falls back to
    # LANGFUSE_HOST when unset.
    LANGFUSE_PUBLIC_URL: str = ""

    # App
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"

    def validate_required(self) -> None:
        # In gateway mode, ANTHROPIC_API_KEY isn't needed — gateway handles auth.
        required = [k for k in REQUIRED_SETTINGS if not (self.USE_AI_GATEWAY and k == "ANTHROPIC_API_KEY")]
        missing = [key for key in required if not getattr(self, key)]
        if missing:
            raise ValueError(
                f"Missing required configuration: {', '.join(missing)}. Set them in .env or environment variables."
            )

        bad_skip = [r for r in self.SKIP_REVIEW_FOR_DOCS_ONLY if r not in ALLOWED_SCOPE_ROLES]
        if bad_skip:
            raise ValueError(
                f"SKIP_REVIEW_FOR_DOCS_ONLY contains unknown role(s): {bad_skip}. Allowed: {list(ALLOWED_SCOPE_ROLES)}"
            )


settings = Settings()
