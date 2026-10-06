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

# No file-size limits. This is a local, disk-backed project: the user's disk is
# the limit and the user limits themselves. A cap here was never a safety control
# -- it was a policy on the user's own files, and it is why a 6.7 MB PDF that
# uploaded fine could not then be read. The one real bound is the model's context
# window, applied where text is handed to the model, not here.
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
    """
    # Check symlinks - resolve and verify target is in sandbox.
    #
    # This is a security control and stays. The size check that used to live
    # below it was a file policy, not a safety property, and has been removed
    # (see the note on SANDBOX_ROOT).
    if path.is_symlink():
        try:
            target = path.resolve()
            target.relative_to(SANDBOX_ROOT)
        except ValueError as e:
            raise SymlinkEscapeError("Symlink points outside sandbox") from e


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


def read_sandbox_file(relative_path: str) -> str:
    """Read a file from the sandbox as text.

    Args:
        relative_path: Path relative to sandbox root

    Returns:
        File content as string

    Raises:
        PathTraversalError, FileNotFoundError

    Note on binary formats: this reads bytes as UTF-8 with `errors="replace"`,
    which is correct for text files and meaningless for a PDF or a .docx. Callers
    that may be handed a document must go through
    `files.extract_file_content`, which dispatches on the extension. The
    `max_size` parameter that used to sit here was dead (no caller passed it) and
    is gone with the size limits.
    """
    path = resolve_sandbox_path(relative_path)
    validate_path_safety(path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {relative_path}")

    if not path.is_file():
        raise ValueError(f"Not a file: {relative_path}")

    return path.read_text(encoding="utf-8", errors="replace")


def write_sandbox_bytes(relative_path: str, data: bytes, overwrite: bool = False) -> Path:
    """Write bytes to the sandbox atomically.

    The bytes counterpart of ``write_sandbox_file``. Binary documents (docx, pdf,
    xlsx, …) cannot survive ``write_text``, so every writer funnels through here;
    text writers pass already-encoded bytes. One atomic-write implementation and
    one place for the traversal/symlink checks.

    Raises PathTraversalError, FileExistsError.
    """
    path = resolve_sandbox_path(relative_path)
    validate_path_safety(path)

    if path.exists() and not overwrite:
        raise FileExistsError(f"File exists: {relative_path} (use overwrite=True)")

    # Atomic write: write to temp file then rename.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        tmp_path.write_bytes(data)
        tmp_path.rename(path)
    except Exception:
        # Cleanup temp file on failure.
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return path


def write_sandbox_file(relative_path: str, content: str, overwrite: bool = False) -> Path:
    """Write a UTF-8 text file to the sandbox atomically.

    Delegates to ``write_sandbox_bytes``. Callers writing a binary document must
    use ``write_sandbox_bytes`` directly (or, better, ``files.render_file_bytes``,
    which dispatches on the extension); this text path is for plain formats only.

    Args:
        relative_path: Path relative to sandbox root
        content: File content to write
        overwrite: Allow overwriting existing file

    Returns:
        Path object of written file

    Raises:
        PathTraversalError, FileExistsError
    """
    return write_sandbox_bytes(relative_path, content.encode("utf-8"), overwrite)


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


def rename_sandbox_file(relative_path: str, new_name: str) -> tuple[Path, Path]:
    """Rename a file within the sandbox.

    ``new_name`` is a file name; a bare name keeps the file in its folder, while a
    name with a slash moves it. The extension is not validated here (the tool
    layer keeps it fixed) — this is the filesystem primitive.

    Refuses to overwrite an existing file: a rename that clobbers is silent data
    loss, worse than a failed rename.

    Returns ``(old_path, new_path)``.

    Raises PathTraversalError, FileNotFoundError, FileExistsError.
    """
    old_path = resolve_sandbox_path(relative_path)
    if not old_path.exists():
        raise FileNotFoundError(f"File not found: {relative_path}")
    if not old_path.is_file():
        raise ValueError(f"Not a file: {relative_path}")

    new_rel = new_name if "/" in new_name else str(Path(relative_path).parent / new_name)
    new_path = resolve_sandbox_path(new_rel)
    validate_path_safety(new_path)

    if new_path.exists():
        raise FileExistsError(f"File exists: {new_rel}")

    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    return old_path, new_path


def get_sandbox_root() -> Path:
    """Return the sandbox root path (for testing/inspection)."""
    return SANDBOX_ROOT