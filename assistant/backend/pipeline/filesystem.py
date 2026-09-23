"""Sandbox filesystem operations with safety enforcement.

All file operations go through this module to ensure:
- Path traversal prevention (no ../ escape)
- Symlink validation (no links pointing outside sandbox)
- Size limits (configurable per operation)
- Atomic writes (temp file + rename)
"""

from pathlib import Path

from assistant.backend.config import settings

# Sandbox root - all paths resolved relative to this
SANDBOX_ROOT = Path("/app/data").resolve()

# Size limits (can be overridden via settings)
MAX_FILE_SIZE = getattr(settings, "sandbox_max_file_size", 10_000_000)      # 10MB write
MAX_READ_SIZE = getattr(settings, "sandbox_max_read_size", 1_000_000)       # 1MB read
MAX_GLOB_RESULTS = getattr(settings, "sandbox_max_glob_results", 1000)      # 1000 results


class SandboxError(Exception):
    """Base exception for sandbox violations."""
    pass


class PathTraversalError(SandboxError):
    """Raised when a path attempts to escape the sandbox."""
    pass


class SymlinkEscapeError(SandboxError):
    """Raised when a symlink points outside the sandbox."""
    pass


class SizeLimitError(SandboxError):
    """Raised when a file exceeds size limits."""
    pass


def resolve_sandbox_path(relative_path: str) -> Path:
    """Resolve a relative path within the sandbox, preventing traversal.

    Args:
        relative_path: Path relative to sandbox root (e.g., "notes/todo.txt")

    Returns:
        Absolute Path object within sandbox

    Raises:
        PathTraversalError: If path escapes sandbox
        ValueError: If path is empty or invalid
    """
    if not relative_path or not relative_path.strip():
        raise ValueError("Path cannot be empty")

    # Normalize: collapse .. and . but don't resolve symlinks yet
    # We want to check the logical path before following symlinks
    requested = (SANDBOX_ROOT / relative_path).resolve()

    # Verify the resolved path is within sandbox
    try:
        requested.relative_to(SANDBOX_ROOT)
    except ValueError as e:
        raise PathTraversalError(f"Path '{relative_path}' escapes sandbox") from e

    return requested


def validate_path_safety(path: Path) -> None:
    """Additional safety checks on a resolved path.

    Args:
        path: Already resolved absolute path

    Raises:
        SymlinkEscapeError: If symlink points outside sandbox
        SizeLimitError: If existing file exceeds write limit
    """
    # Check symlinks - resolve and verify target is in sandbox
    if path.is_symlink():
        try:
            target = path.resolve()
            target.relative_to(SANDBOX_ROOT)
        except ValueError as e:
            raise SymlinkEscapeError("Symlink points outside sandbox") from e

    # Check size of existing file (for write operations)
    if path.exists() and path.is_file():
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            raise SizeLimitError(f"File exceeds {MAX_FILE_SIZE} byte limit ({size} bytes)")


def list_sandbox_files(pattern: str = "**/*") -> list[dict]:
    """List files matching a glob pattern within the sandbox.

    Args:
        pattern: Glob pattern relative to sandbox root (e.g., "*.txt", "notes/**/*.md")

    Returns:
        List of file dicts with path, size, modified time, extension
    """
    # Security: ensure pattern doesn't escape via parent traversal in the pattern itself
    # by checking the base directory of the pattern
    try:
        # Get the non-glob prefix of the pattern
        pattern_parts = Path(pattern).parts
        # If pattern starts with .. or uses absolute path, reject
        if any(p == ".." for p in pattern_parts) or Path(pattern).is_absolute():
            raise PathTraversalError(f"Glob pattern '{pattern}' attempts traversal")
    except PathTraversalError:
        raise
    except Exception:
        # If we can't parse pattern safely, default to listing root
        pattern = "**/*"

    matches = list(SANDBOX_ROOT.glob(pattern))
    files = []

    for m in matches[:MAX_GLOB_RESULTS]:
        if m.is_file():
            try:
                rel = m.relative_to(SANDBOX_ROOT)
                files.append({
                    "path": str(rel),
                    "size": m.stat().st_size,
                    "modified": m.stat().st_mtime,
                    "ext": m.suffix.lstrip(".").lower()
                })
            except ValueError:
                # Skip files that somehow escaped (shouldn't happen)
                continue

    return files


def read_sandbox_file(relative_path: str, max_size: int | None = None) -> str:
    """Read a file from the sandbox.

    Args:
        relative_path: Path relative to sandbox root
        max_size: Override max read size (default: MAX_READ_SIZE)

    Returns:
        File content as string

    Raises:
        PathTraversalError, FileNotFoundError, SizeLimitError
    """
    path = resolve_sandbox_path(relative_path)
    validate_path_safety(path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {relative_path}")

    if not path.is_file():
        raise ValueError(f"Not a file: {relative_path}")

    limit = max_size or MAX_READ_SIZE
    if path.stat().st_size > limit:
        raise SizeLimitError(f"File exceeds read limit of {limit} bytes")

    return path.read_text(encoding="utf-8", errors="replace")


def write_sandbox_file(relative_path: str, content: str, overwrite: bool = False) -> Path:
    """Write a file to the sandbox atomically.

    Args:
        relative_path: Path relative to sandbox root
        content: File content to write
        overwrite: Allow overwriting existing file

    Returns:
        Path object of written file

    Raises:
        PathTraversalError, FileExistsError, SizeLimitError
    """
    path = resolve_sandbox_path(relative_path)
    validate_path_safety(path)

    if path.exists() and not overwrite:
        raise FileExistsError(f"File exists: {relative_path} (use overwrite=True)")

    # Check content size before writing
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_FILE_SIZE:
        raise SizeLimitError(
            f"Content exceeds {MAX_FILE_SIZE} byte limit ({len(content_bytes)} bytes)"
        )

    # Atomic write: write to temp file then rename
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.rename(path)
    except Exception:
        # Cleanup temp file on failure
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return path


def delete_sandbox_file(relative_path: str) -> Path:
    """Delete a file from the sandbox.

    Args:
        relative_path: Path relative to sandbox root

    Returns:
        Path object of deleted file

    Raises:
        PathTraversalError, FileNotFoundError
    """
    path = resolve_sandbox_path(relative_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {relative_path}")

    if not path.is_file():
        raise ValueError(f"Not a file: {relative_path}")

    path.unlink()
    return path


def get_sandbox_root() -> Path:
    """Return the sandbox root path (for testing/inspection)."""
    return SANDBOX_ROOT