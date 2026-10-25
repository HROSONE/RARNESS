"""Núcleo do RARNESS: planejamento, inteligência de código, memória, arquivos, execução, correção, Git e estado do projeto."""

from rarness.code_intel import (
    apply_multi_edit,
    apply_surgical_edit,
    build_repo_map,
    format_numbered_lines,
    search_codebase,
    unified_diff_preview,
)
from rarness.git_ops import git_diff_summary, git_status_summary, restore_latest_backup
from rarness.memory import (
    TodoItem,
    append_memory_note,
    compact_history,
    expand_at_mentions,
    format_todos,
    generate_claude_md,
    load_claude_memories,
    load_todos,
    save_todos,
)

__all__ = [
    "TodoItem",
    "append_memory_note",
    "apply_multi_edit",
    "apply_surgical_edit",
    "build_repo_map",
    "compact_history",
    "expand_at_mentions",
    "format_numbered_lines",
    "format_todos",
    "generate_claude_md",
    "git_diff_summary",
    "git_status_summary",
    "load_claude_memories",
    "load_todos",
    "restore_latest_backup",
    "save_todos",
    "search_codebase",
    "unified_diff_preview",
]
