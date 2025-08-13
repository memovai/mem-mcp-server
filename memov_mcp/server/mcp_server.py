#!/usr/bin/env python3
"""
Memov MCP Server - AI-assisted version control with automatic prompt recording

This MCP server provides intelligent memov integration that automatically:
- Records user prompts with file changes
- Handles new files vs modified files appropriately
- Provides seamless version control for AI-assisted development

Author: Memov Team
License: MIT
"""

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from ..utils.agent_capture import capture_change, capture_plan, get_agent_capture
from ..utils.summarizer import create_summary_from_commits

LOGGER = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("Memov MCP Server")

# Global context storage for user prompts and working directory
_user_context = {
    "current_prompt": None,
    "timestamp": None,
    "session_id": None,
    "working_directory": None,
}


def run_mem_command(args: list[str], project_path: str = None) -> Dict[str, Any]:
    """
    Execute a mem command and return structured result
    """
    # If no project_path is provided or it's just ".", check environment variable first
    if project_path is None or project_path == ".":
        # Check for MEMOV_DEFAULT_PROJECT environment variable first
        memov_project = os.environ.get("MEMOV_DEFAULT_PROJECT")
        if memov_project and os.path.exists(memov_project):
            project_path = memov_project
            LOGGER.info(f"Using MEMOV_DEFAULT_PROJECT environment variable: {project_path}")
        else:
            global _user_context
            if _user_context["working_directory"]:
                project_path = _user_context["working_directory"]
                LOGGER.info(f"Using user's set working directory: {project_path}")
            else:
                # Check if PWD environment variable is set (user's actual working directory)
                user_pwd = os.environ.get("PWD")
                if user_pwd and os.path.exists(user_pwd):
                    project_path = user_pwd
                else:
                    # Fallback to current working directory
                    project_path = os.getcwd()

                LOGGER.info(f"Auto-detected project path: {project_path}")

    # Ensure project_path is absolute and exists
    abs_project_path = os.path.abspath(project_path)
    if not os.path.exists(abs_project_path):
        try:
            os.makedirs(abs_project_path, exist_ok=True)
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"Failed to create project directory {abs_project_path}: {str(e)}",
                "command": f"mem --loc {abs_project_path} {' '.join(args)}",
            }

    # Build command with project path - --loc should come after subcommand
    cmd = ["mem"] + args + ["--loc", abs_project_path]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=abs_project_path, timeout=30
        )

        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip(),
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": "Command timed out after 30 seconds",
            "command": " ".join(cmd),
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Failed to execute command: {str(e)}",
            "command": " ".join(cmd),
        }


# Core MCP tools for intelligent memov integration


@mcp.tool()
def set_user_context(
    user_prompt: str, session_id: Optional[str] = None, project_path: str = None
) -> str:
    """
    Set the current user context for automatic tracking.

    **IMPORTANT: Call this tool FIRST when user makes any request.**

    **When to use this tool:**
    - At the beginning of any coding task
    - When user asks to modify, create, or delete files
    - When user requests new features or bug fixes
    - Before starting any development work

    **Example usage:**
    User: "Modify the content of 1.txt to become 2"
    → First call: set_user_context("Modify the content of 1.txt to become 2")
    → Then: perform the file modification
    → Finally: auto_mem_snap("1.txt")

    Args:
        user_prompt: The user's exact original prompt/request
        session_id: Optional session identifier
        project_path: Path to the project directory (default: user's current working directory)

    Returns:
        Confirmation message
    """
    try:
        LOGGER.info(
            f"set_user_context called with: user_prompt='{user_prompt}', session_id='{session_id}'"
        )

        # Capture this user request as a plan
        try:
            plan_result = capture_plan("user_request", user_prompt, project_path)
            LOGGER.info(f"Auto-captured user request: {plan_result}")
        except Exception as e:
            LOGGER.error(f"Failed to auto-capture user request: {e}")
        global _user_context
        _user_context["current_prompt"] = user_prompt
        _user_context["timestamp"] = time.time()
        _user_context["session_id"] = session_id or str(int(time.time()))

        result = f"✅ User context set: {user_prompt[:100]}{'...' if len(user_prompt) > 100 else ''}"
        LOGGER.info(f"set_user_context result: {result}")
        return result
    except Exception as e:
        LOGGER.error(f"Error in set_user_context: {e}", exc_info=True)
        return f"❌ Error setting user context: {str(e)}"


@mcp.tool()
def auto_mem_snap(files_changed: str = "", project_path: str = None) -> str:
    """
    Automatically create a mem snap using the stored user context with intelligent workflow.

    **IMPORTANT: Call this tool AFTER completing any file modifications, code changes,
    or task completion to automatically record the user's request and track changed files.**

    **Intelligent Workflow:**
    1. **Auto-initialize** - Creates memov repository if it doesn't exist
    2. **Status check** - Analyzes current file states (untracked, modified, clean)
    3. **Smart handling** -
       - **New files** → `mem track` (auto-commits with prompt)
       - **Modified files** → `mem snap` (records changes with prompt)
    4. **No conflicts** - Avoids redundant operations

    **When to use this tool:**
    - After modifying, creating, or deleting files
    - After completing user's coding requests
    - After making configuration changes
    - After completing any development task

    **When NOT to use this tool:**
    - For read-only operations (viewing files, searching)
    - For informational queries
    - Before making changes (use set_user_context first)
    - **After rename operations** - `mem rename` already handles the recording
    - **After remove operations** - `mem remove` already handles the recording

    Args:
        files_changed: Comma-separated list of files that were modified/created/deleted (optional but recommended)
        project_path: Path to the project directory with corresponding format (default: user's current working directory)
            Examples format:
                - Unix/macOS: `/home/user/my_project`
                - Windows: `D:/Projects/my_project`

    Returns:
        Detailed result of the complete workflow execution
    """
    try:
        LOGGER.info(
            f"auto_mem_snap called with: files_changed='{files_changed}', project_path='{project_path}'"
        )
        global _user_context

        if not _user_context["current_prompt"]:
            result = (
                "❌ No user context available. Please set user context first using set_user_context."
            )
            LOGGER.warning(result)
            return result

        prompt = _user_context["current_prompt"]
        LOGGER.info(f"Using prompt: {prompt}")

        # Step 1: Check if Memov is initialized
        status_result = run_mem_command(["status"], project_path)
        LOGGER.info(f"Status result: {status_result}")

        if not status_result["success"]:
            if (
                "does not exist" in status_result["error"]
                or "not initialized" in status_result["error"]
            ):
                # Auto-initialize if not exists
                init_result = run_mem_command(["init"], project_path)
                if not init_result["success"]:
                    return f"❌ Failed to initialize Memov in {project_path}: {init_result['error']}\n💡 Make sure you're in the correct project directory or the directory has write permissions."

                # Get status after initialization
                status_result = run_mem_command(["status"], project_path)
                LOGGER.info(f"Status after init: {status_result}")
            else:
                return f"❌ Memov status check failed: {status_result['error']}"

        # Step 2: Parse status to understand file states
        def clean_ansi_codes(text):
            """Remove ANSI color codes from text"""
            ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
            return ansi_escape.sub("", text)

        file_states = {}
        if status_result["success"]:
            # Check both stdout and stderr for status information
            status_output = status_result["output"] + "\n" + status_result["error"]
            for line in status_output.split("\n"):
                line = line.strip()
                if not line:
                    continue

                if "Untracked:" in line:
                    filename = clean_ansi_codes(line.split("Untracked:")[-1].strip())
                    if filename:
                        file_states[filename] = "untracked"
                elif "Modified:" in line:
                    filename = clean_ansi_codes(line.split("Modified:")[-1].strip())
                    if filename:
                        file_states[filename] = "modified"
                elif "Clean:" in line:
                    filename = clean_ansi_codes(line.split("Clean:")[-1].strip())
                    if filename:
                        file_states[filename] = "clean"

        LOGGER.info(f"File states: {file_states}")

        # Step 3: Handle files based on their states
        tracked_files = []
        modified_files = []

        if files_changed.strip():
            file_list = [f.strip() for f in files_changed.split(",") if f.strip()]

            # Check the file states
            untracked_files = []
            modified_files = []

            # If we have no file_states (parsing failed), fall back to checking file existence
            if not file_states:
                LOGGER.warning(
                    "No file states detected from status output, falling back to file existence check"
                )
                for f in file_list:
                    file_path_to_check = (
                        f if os.path.isabs(f) else os.path.join(project_path or os.getcwd(), f)
                    )
                    if os.path.exists(file_path_to_check):
                        LOGGER.info(f"File {f} exists, assuming it needs tracking")
                        untracked_files.append(f)
            else:
                # Normal path: match files against status output
                for f in file_list:
                    # Normalize the file path for comparison
                    f_normalized = os.path.normpath(f)
                    f_abs = os.path.abspath(f) if project_path else f

                    found_match = False
                    for file_name, state in file_states.items():
                        # Try multiple comparison methods to ensure we find matches
                        file_name_normalized = os.path.normpath(file_name)
                        file_name_abs = (
                            os.path.abspath(file_name) if os.path.isabs(file_name) else file_name
                        )

                        # Compare in multiple ways to handle different path representations
                        if (
                            f_normalized == file_name_normalized
                            or f == file_name
                            or f_abs == file_name_abs
                            or os.path.basename(f) == os.path.basename(file_name)
                        ):
                            if state == "untracked":
                                untracked_files.append(f)
                            elif state == "modified":
                                modified_files.append(f)
                            found_match = True
                            break

                    # If no match found in file_states but file exists, assume it's untracked
                    if not found_match:
                        file_path_to_check = (
                            f if os.path.isabs(f) else os.path.join(project_path or os.getcwd(), f)
                        )
                        if os.path.exists(file_path_to_check):
                            LOGGER.info(f"File {f} not found in status output, assuming untracked")
                            untracked_files.append(f)

            # Track untracked files (this automatically commits them)
            if untracked_files:
                LOGGER.info(f"Tracking untracked files: {untracked_files}")
                track_result = run_mem_command(
                    ["track"] + untracked_files + ["-p", prompt], project_path
                )
                LOGGER.info(f"Track result: {track_result}")
                if not track_result["success"]:
                    return f"❌ Failed to track files: {track_result['error']}"
                LOGGER.info(f"Successfully tracked files: {untracked_files}")
                tracked_files = untracked_files

            # Only snap if there are modified files (not untracked)
            if modified_files:
                LOGGER.info(f"Creating snapshot for modified files: {modified_files}")
                snap_result = run_mem_command(["snap", "-p", prompt], project_path)
                LOGGER.info(f"Snapshot result: {snap_result}")
                if not snap_result["success"]:
                    return f"❌ Failed to create snapshot: {snap_result['error']}"
                LOGGER.info(f"Successfully snapped modified files: {modified_files}")
            elif not untracked_files:
                # No files to process, but still create a snapshot
                LOGGER.info("No specific files provided, creating general snapshot")
                snap_result = run_mem_command(["snap", "-p", prompt], project_path)
                LOGGER.info(f"General snapshot created: {snap_result}")
                if not snap_result["success"]:
                    return f"❌ Failed to create snapshot: {snap_result['error']}"

        # Capture code changes
        try:
            if tracked_files:
                for file in tracked_files:
                    change_result = capture_change(
                        "create", file, f"Tracked new file: {prompt[:100]}", project_path
                    )
                    LOGGER.info(f"Auto-captured file creation: {change_result}")
            if modified_files:
                for file in modified_files:
                    change_result = capture_change(
                        "modify", file, f"Modified file: {prompt[:100]}", project_path
                    )
                    LOGGER.info(f"Auto-captured file modification: {change_result}")
        except Exception as e:
            LOGGER.error(f"Failed to auto-capture code changes: {e}")

        # Clear context after successful operation
        _user_context["current_prompt"] = None

        # Build detailed result message
        result_parts = ["✅ Auto operation completed successfully"]
        result_parts.append(f"📝 Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")

        if tracked_files:
            result_parts.append(f"📂 Tracked new files: {', '.join(tracked_files)}")
        if modified_files:
            result_parts.append(f"📝 Recorded changes to: {', '.join(modified_files)}")

        result_parts.append(f"📁 Project: {project_path}")

        result = "\n".join(result_parts)
        LOGGER.info(f"Operation completed successfully: {result}")
        return result

    except Exception as e:
        error_msg = f"❌ Error creating auto snapshot: {str(e)}"
        LOGGER.error(error_msg, exc_info=True)
        return error_msg


@mcp.tool()
def auto_mem_rename(old_path: str, new_path: str, project_path: str = None) -> str:
    """
    Automatically rename a file using mem rename with intelligent workflow.

    **IMPORTANT: This tool automatically handles the complete rename workflow including recording
    the operation. No need to call auto_mem_snap afterward as mem rename already handles
    the recording of the change.**

    **Intelligent Workflow:**
    1. **Auto-initialize** - Creates memov repository if it doesn't exist
    2. **Validation** - Checks if source file exists and target doesn't conflict
    3. **Smart rename** - Uses `mem rename` which automatically:
       - Moves the file from old_path to new_path
       - Records the rename operation with the user's prompt
       - Handles memov tracking automatically
    4. **Uses context** - Leverages stored user context for meaningful commit messages

    **When to use this tool:**
    - When user asks to rename files
    - When refactoring and need to move files with proper tracking
    - When organizing project structure
    - Any time a file needs to be moved with version control tracking

    **Usage Examples:**
    - User: "Rename old.py to new.py for clarity"
      → First: set_user_context("Rename old.py to new.py for clarity")
      → Then: auto_mem_rename("old.py", "new.py")

    Args:
        old_path: Current path of the file to rename
        new_path: New path/name for the file
        project_path: Path to the project directory (default: user's current working directory)
            Examples format:
                - Unix/macOS: `/home/user/my_project`
                - Windows: `D:/Projects/my_project`

    Returns:
        Detailed result of the rename operation
    """
    try:
        LOGGER.info(
            f"auto_mem_rename called with: old_path='{old_path}', new_path='{new_path}', project_path='{project_path}'"
        )
        global _user_context

        # Use default prompt if no context is set
        if not _user_context["current_prompt"]:
            prompt = f"Rename {old_path} to {new_path}"
            LOGGER.info(f"No user context available, using default prompt: {prompt}")
        else:
            prompt = _user_context["current_prompt"]
            LOGGER.info(f"Using stored user prompt: {prompt}")

        # Step 1: Check if Memov is initialized
        status_result = run_mem_command(["status"], project_path)
        LOGGER.info(f"Status result: {status_result}")

        if not status_result["success"]:
            if (
                "does not exist" in status_result["error"]
                or "not initialized" in status_result["error"]
            ):
                # Auto-initialize if not exists
                init_result = run_mem_command(["init"], project_path)
                if not init_result["success"]:
                    return f"❌ Failed to initialize Memov in {project_path}: {init_result['error']}\n💡 Make sure you're in the correct project directory or the directory has write permissions."

                LOGGER.info("Memov repository initialized successfully")
            else:
                return f"❌ Memov status check failed: {status_result['error']}"

        # Step 2: Validate paths
        if project_path:
            abs_project_path = os.path.abspath(project_path)
            abs_old_path = (
                os.path.join(abs_project_path, old_path)
                if not os.path.isabs(old_path)
                else old_path
            )
            abs_new_path = (
                os.path.join(abs_project_path, new_path)
                if not os.path.isabs(new_path)
                else new_path
            )
        else:
            abs_old_path = os.path.abspath(old_path)
            abs_new_path = os.path.abspath(new_path)

        # Check if source file exists
        if not os.path.exists(abs_old_path):
            return f"❌ Source file does not exist: {old_path}"

        # Check if target already exists
        if os.path.exists(abs_new_path):
            return f"❌ Target file already exists: {new_path}"

        # Step 3: Execute mem rename command
        LOGGER.info(f"Executing mem rename from {old_path} to {new_path} with prompt: {prompt}")
        rename_result = run_mem_command(["rename", old_path, new_path, "-p", prompt], project_path)
        LOGGER.info(f"Rename result: {rename_result}")

        if not rename_result["success"]:
            return f"❌ Failed to rename file: {rename_result['error']}"

        # Clear context after successful operation (if it was set)
        if _user_context["current_prompt"]:
            _user_context["current_prompt"] = None

        # Build success message
        result_parts = ["✅ File renamed successfully"]
        result_parts.append(f"📁 {old_path} → {new_path}")
        result_parts.append(f"📝 Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        result_parts.append(f"📂 Project: {project_path or 'current directory'}")
        result_parts.append("🔄 Rename operation recorded in Memov history")

        result = "\n".join(result_parts)
        LOGGER.info(f"Rename operation completed successfully: {result}")
        return result

    except Exception as e:
        error_msg = f"❌ Error during file rename: {str(e)}"
        LOGGER.error(error_msg, exc_info=True)
        return error_msg


@mcp.tool()
def auto_mem_remove(file_path: str, reason: str = "", project_path: str = None) -> str:
    """
    Automatically remove a file using mem remove with intelligent workflow.

    **IMPORTANT: This tool automatically handles the complete remove workflow including recording
    the operation. No need to call auto_mem_snap afterward as mem remove already handles
    the recording of the change.**

    **Intelligent Workflow:**
    1. **Auto-initialize** - Creates memov repository if it doesn't exist
    2. **Validation** - Checks if file exists before removal
    3. **Smart remove** - Uses `mem remove` which automatically:
       - Removes the file from filesystem
       - Records the remove operation with the user's prompt and reason
       - Handles memov tracking automatically
    4. **Uses context** - Leverages stored user context for meaningful commit messages

    **When to use this tool:**
    - When user asks to delete/remove files
    - When cleaning up unused files with proper tracking
    - When removing legacy code or temporary files
    - Any time a file needs to be deleted with version control tracking

    **Usage Examples:**
    - User: "Remove legacy.py, it's no longer used"
      → First: set_user_context("Remove legacy.py, it's no longer used")
      → Then: auto_mem_remove("legacy.py", "No longer used")

    Args:
        file_path: Path of the file to remove
        reason: Additional reason for removal (optional but recommended)
        project_path: Path to the project directory (default: user's current working directory)
            Examples format:
                - Unix/macOS: `/home/user/my_project`
                - Windows: `D:/Projects/my_project`

    Returns:
        Detailed result of the remove operation
    """
    try:
        LOGGER.info(
            f"auto_mem_remove called with: file_path='{file_path}', reason='{reason}', project_path='{project_path}'"
        )
        global _user_context

        # Use default prompt if no context is set
        if not _user_context["current_prompt"]:
            prompt = f"Remove {file_path}"
            if reason:
                prompt += f": {reason}"
            LOGGER.info(f"No user context available, using default prompt: {prompt}")
        else:
            prompt = _user_context["current_prompt"]
            LOGGER.info(f"Using stored user prompt: {prompt}")

        # Step 1: Check if Memov is initialized
        status_result = run_mem_command(["status"], project_path)
        LOGGER.info(f"Status result: {status_result}")

        if not status_result["success"]:
            if (
                "does not exist" in status_result["error"]
                or "not initialized" in status_result["error"]
            ):
                # Auto-initialize if not exists
                init_result = run_mem_command(["init"], project_path)
                if not init_result["success"]:
                    return f"❌ Failed to initialize Memov in {project_path}: {init_result['error']}\n💡 Make sure you're in the correct project directory or the directory has write permissions."

                LOGGER.info("Memov repository initialized successfully")
            else:
                return f"❌ Memov status check failed: {status_result['error']}"

        # Step 2: Validate file exists
        if project_path:
            abs_project_path = os.path.abspath(project_path)
            abs_file_path = (
                os.path.join(abs_project_path, file_path)
                if not os.path.isabs(file_path)
                else file_path
            )
        else:
            abs_file_path = os.path.abspath(file_path)

        # Check if file exists
        if not os.path.exists(abs_file_path):
            return f"❌ File does not exist: {file_path}"

        # Step 3: Build mem remove command
        cmd_args = ["remove", file_path, "-p", prompt]
        if reason:
            cmd_args.extend(["-r", reason])

        # Execute mem remove command
        LOGGER.info(
            f"Executing mem remove for {file_path} with prompt: {prompt} and reason: {reason}"
        )
        remove_result = run_mem_command(cmd_args, project_path)
        LOGGER.info(f"Remove result: {remove_result}")

        if not remove_result["success"]:
            return f"❌ Failed to remove file: {remove_result['error']}"

        # Clear context after successful operation (if it was set)
        if _user_context["current_prompt"]:
            _user_context["current_prompt"] = None

        # Build success message
        result_parts = ["✅ File removed successfully"]
        result_parts.append(f"🗑️ Removed: {file_path}")
        result_parts.append(f"📝 Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        if reason:
            result_parts.append(f"💬 Reason: {reason}")
        result_parts.append(f"📂 Project: {project_path or 'current directory'}")
        result_parts.append("🔄 Remove operation recorded in Memov history")

        result = "\n".join(result_parts)
        LOGGER.info(f"Remove operation completed successfully: {result}")
        return result

    except Exception as e:
        error_msg = f"❌ Error during file removal: {str(e)}"
        LOGGER.error(error_msg, exc_info=True)
        return error_msg


# Additional utility tools


@mcp.tool()
def get_user_context() -> str:
    """
    Get the current user context

    Returns:
        Current user context information
    """
    global _user_context

    if not _user_context["current_prompt"]:
        return "📭 No user context currently set"

    return f"📋 Current user context:\n🗣️ Prompt: {_user_context['current_prompt']}\n⏰ Set at: {time.ctime(_user_context['timestamp'])}\n🔑 Session: {_user_context['session_id']}\n📁 Working directory: {_user_context['working_directory'] or 'Not set'}"


@mcp.tool()
def set_working_directory(directory_path: str) -> str:
    """
    Set the working directory for memov operations

    Args:
        directory_path: Path to the directory where memov should operate

    Returns:
        Confirmation message
    """
    try:
        global _user_context
        abs_path = os.path.abspath(directory_path)

        if not os.path.exists(abs_path):
            return f"❌ Directory does not exist: {abs_path}"

        if not os.path.isdir(abs_path):
            return f"❌ Path is not a directory: {abs_path}"

        _user_context["working_directory"] = abs_path
        LOGGER.info(f"Working directory set to: {abs_path}")

        return f"✅ Working directory set to: {abs_path}"

    except Exception as e:
        error_msg = f"❌ Error setting working directory: {str(e)}"
        LOGGER.error(error_msg, exc_info=True)
        return error_msg


@mcp.tool()
def mem_history(project_path: str = None) -> str:
    """
    Show history of snapshots and interactions

    Args:
        project_path: Path to the project directory (default: user's current working directory)

    Returns:
        History of snapshots and interactions
    """
    result = run_mem_command(["history"], project_path)

    if result["success"]:
        return f"📚 Memov History:\n{result['output']}"
    else:
        return f"❌ Failed to get history: {result['error']}"


@mcp.tool()
def share(project_path: str = None) -> str:
    """
    Create a comprehensive summary of recent work by analyzing the last 10 commits.
    This function collects context from recent commits and creates a comprehensive summary
    using OpenAI API that is saved to .mem/share/ directory as JSON.

    The function will:
    1. Get the last 10 commits from mem history
    2. Collect detailed information for each commit using mem show
    3. Generate AI-powered summary using OpenAI API
    4. Save a structured summary to .mem/share/ directory as JSON

    Args:
        project_path: Path to the project directory (default: user's current working directory)

    Returns:
        Result message indicating success/failure and location of saved summary
    """
    try:
        # Get history
        history_result = run_mem_command(["history"], project_path)
        if not history_result["success"]:
            return f"❌ Failed to get history: {history_result['error']}"

        # Parse history to get last 10 commits
        history_lines = history_result["output"].strip().split("\n")
        # Skip header lines and get commit entries
        commit_lines = [
            line
            for line in history_lines
            if line and not line.startswith("Operation") and not line.startswith("------")
        ]

        # Get last 10 commits (or all if less than 10)
        recent_commits = commit_lines[:10]

        commit_details = []
        for line in recent_commits:
            # Parse the line to extract commit hash
            # Format: Operation  Branch  Commit  Prompt  Response
            parts = line.split()
            if len(parts) >= 3:
                commit_hash = parts[2]  # Third column is commit hash

                # Get detailed info for this commit
                show_result = run_mem_command(["show", commit_hash], project_path)
                if show_result["success"]:
                    commit_details.append(
                        {
                            "commit_hash": commit_hash,
                            "summary_line": line.strip(),
                            "details": show_result["output"],
                        }
                    )

        # Generate summary using the separated summarizer module
        summary = create_summary_from_commits(commit_details, use_ai=True)

        # Create summary data with the generated summary
        summary_data = {
            "timestamp": time.time(),
            "project_path": project_path or os.getcwd(),
            "commits_analyzed": len(commit_details),
            "commit_hashes": [commit["commit_hash"] for commit in commit_details],
            "summary": summary,
        }

        # Ensure .mem/share directory exists
        if project_path:
            abs_project_path = os.path.abspath(project_path)
        else:
            abs_project_path = os.getcwd()

        # Create .mem directory first
        mem_dir = os.path.join(abs_project_path, ".mem")
        try:
            os.makedirs(mem_dir, exist_ok=True)
            LOGGER.info(f"Created/verified .mem directory: {mem_dir}")
        except Exception as e:
            LOGGER.error(f"Failed to create .mem directory: {e}")
            return f"❌ Failed to create .mem directory: {str(e)}"

        # Create share subdirectory
        share_dir = os.path.join(mem_dir, "share")
        try:
            os.makedirs(share_dir, exist_ok=True)
            LOGGER.info(f"Created/verified share directory: {share_dir}")
        except Exception as e:
            LOGGER.error(f"Failed to create share directory: {e}")
            return f"❌ Failed to create .mem/share directory: {str(e)}"

        # Save to JSON file with timestamp
        timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        filename = f"share_summary_{timestamp_str}.json"
        filepath = os.path.join(share_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)

        return f"✅ Share summary created successfully!\n📁 Saved to: {filepath}\n📊 Analyzed {len(commit_details)} commits\n🤖 AI-powered detailed summary generated"

    except Exception as e:
        error_msg = f"❌ Error creating share summary: {str(e)}"
        LOGGER.error(error_msg, exc_info=True)
        return error_msg


# Agent Capture Tools - Using mem commands to store plans and changes


@mcp.tool()
def record_agent_plan(plan_type: str, content: str, project_path: str = None) -> str:
    """
    Capture an agent auto-plan or thinking step using mem snap.

    **Use this tool to record:**
    - Todo lists and planning steps
    - Strategic decisions and analysis
    - Thinking processes and reasoning

    Args:
        plan_type: Type of plan ('todo', 'thinking', 'strategy', 'analysis', 'decision')
        content: The actual plan or thinking content
        project_path: Path to the project directory (default: user's current working directory)

    Returns:
        Result message from mem command
    """
    result = capture_plan(plan_type, content, project_path)
    LOGGER.info(f"record_agent_plan result: {result}")
    return result


@mcp.tool()
def record_code_change(
    change_type: str, file_path: str, description: str = None, project_path: str = None
) -> str:
    """
    Capture a code change using appropriate mem command.

    **Use this tool to record:**
    - File creation, modification, or deletion
    - Code refactoring and restructuring
    - Configuration changes

    Args:
        change_type: Type of change ('create', 'modify', 'delete', 'rename')
        file_path: Path to the file being changed
        description: Optional description of what changed
        project_path: Path to the project directory (default: user's current working directory)

    Returns:
        Result message from mem command
    """
    result = capture_change(change_type, file_path, description, project_path)
    LOGGER.info(f"record_code_change result: {result}")
    return result


@mcp.tool()
def view_capture_history(project_path: str = None) -> str:
    """
    View recent capture history from mem.

    Args:
        project_path: Path to the project directory (default: user's current working directory)

    Returns:
        Recent history from mem command
    """
    try:
        capture = get_agent_capture(project_path)
        return capture.get_recent_history(project_path)

    except Exception as e:
        error_msg = f"❌ Error getting capture history: {str(e)}"
        LOGGER.error(error_msg, exc_info=True)
        return error_msg


def main():
    """Main entry point for the MCP server"""
    mcp.run()


if __name__ == "__main__":
    main()
