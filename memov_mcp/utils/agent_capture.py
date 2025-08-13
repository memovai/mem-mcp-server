#!/usr/bin/env python3
"""
Agent Capture System - Captures auto-plans and code changes using mem commands

This module provides functionality to capture every auto-plan (todo/thinking) 
the agent makes and each code change directly through mem commands.
"""

import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

LOGGER = logging.getLogger(__name__)


def run_mem_command(args: list[str], project_path: str = None) -> Dict[str, any]:
    """Execute a mem command and return structured result"""
    if project_path is None:
        project_path = os.getcwd()

    abs_project_path = os.path.abspath(project_path)
    if not os.path.exists(abs_project_path):
        os.makedirs(abs_project_path, exist_ok=True)

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


class AgentCapture:
    """
    Captures and stores agent auto-plans and code changes using mem commands
    """

    def __init__(self, project_path: str = None):
        self.project_path = project_path or os.getcwd()
        LOGGER.info(f"AgentCapture initialized for project: {self.project_path}")

    def capture_auto_plan(self, plan_type: str, content: str, project_path: str = None) -> str:
        """
        Capture an auto-plan/thinking step using mem snap

        Args:
            plan_type: Type of plan ('todo', 'thinking', 'strategy', etc.)
            content: The actual plan content
            project_path: Project path override

        Returns:
            Result message
        """
        try:
            # Initialize memov if needed
            status_result = run_mem_command(["status"], project_path or self.project_path)
            if not status_result["success"] and (
                "does not exist" in status_result["error"]
                or "not initialized" in status_result["error"]
            ):
                init_result = run_mem_command(["init"], project_path or self.project_path)
                if not init_result["success"]:
                    return f"❌ Failed to initialize memov: {init_result['error']}"

            # Create a prompt that includes the plan type and content
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            prompt = f"[{plan_type}] {timestamp}: {content}"

            # Use mem snap to record the auto-plan
            snap_result = run_mem_command(["snap", "-p", prompt], project_path or self.project_path)

            if snap_result["success"]:
                LOGGER.info(f"Captured auto-plan [{plan_type}] via mem snap: {content[:100]}...")
                return f"✅ Plan captured via mem: [{plan_type}] {content[:100]}..."
            else:
                LOGGER.error(f"Failed to capture plan: {snap_result['error']}")
                return f"❌ Failed to capture plan: {snap_result['error']}"

        except Exception as e:
            error_msg = f"❌ Error capturing auto-plan: {str(e)}"
            LOGGER.error(error_msg)
            return error_msg

    def capture_code_change(
        self,
        change_type: str,
        file_path: str,
        change_description: str = None,
        project_path: str = None,
    ) -> str:
        """
        Capture a code change using appropriate mem command

        Args:
            change_type: Type of change ('create', 'modify', 'delete', 'rename')
            file_path: Path to the file being changed
            change_description: Description of what changed
            project_path: Project path override

        Returns:
            Result message
        """
        try:
            # Initialize memov if needed
            status_result = run_mem_command(["status"], project_path or self.project_path)
            if not status_result["success"] and (
                "does not exist" in status_result["error"]
                or "not initialized" in status_result["error"]
            ):
                init_result = run_mem_command(["init"], project_path or self.project_path)
                if not init_result["success"]:
                    return f"❌ Failed to initialize memov: {init_result['error']}"

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            description = change_description or f"{change_type} {file_path}"
            prompt = f"[{change_type}] {timestamp}: {description}"

            if change_type == "create":
                # For new files, use mem track
                if os.path.exists(os.path.join(project_path or self.project_path, file_path)):
                    track_result = run_mem_command(
                        ["track", file_path, "-p", prompt], project_path or self.project_path
                    )
                    if track_result["success"]:
                        LOGGER.info(
                            f"Captured code change [{change_type}] via mem track: {file_path}"
                        )
                        return f"✅ Code change captured via mem track: [{change_type}] {file_path}"
                    else:
                        # Fallback to snap
                        snap_result = run_mem_command(
                            ["snap", "-p", prompt], project_path or self.project_path
                        )
                        return (
                            f"✅ Code change captured via mem snap: [{change_type}] {file_path}"
                            if snap_result["success"]
                            else f"❌ Failed: {snap_result['error']}"
                        )
                else:
                    # File doesn't exist yet, just record the intention with snap
                    snap_result = run_mem_command(
                        ["snap", "-p", prompt], project_path or self.project_path
                    )
                    return (
                        f"✅ Code change captured via mem snap: [{change_type}] {file_path}"
                        if snap_result["success"]
                        else f"❌ Failed: {snap_result['error']}"
                    )

            elif change_type in ["modify", "delete", "rename"]:
                # For modifications/deletions, use mem snap
                snap_result = run_mem_command(
                    ["snap", "-p", prompt], project_path or self.project_path
                )
                if snap_result["success"]:
                    LOGGER.info(f"Captured code change [{change_type}] via mem snap: {file_path}")
                    return f"✅ Code change captured via mem snap: [{change_type}] {file_path}"
                else:
                    LOGGER.error(f"Failed to capture change: {snap_result['error']}")
                    return f"❌ Failed to capture change: {snap_result['error']}"
            else:
                # Unknown change type, use generic snap
                snap_result = run_mem_command(
                    ["snap", "-p", prompt], project_path or self.project_path
                )
                return (
                    f"✅ Code change captured via mem snap: [{change_type}] {file_path}"
                    if snap_result["success"]
                    else f"❌ Failed: {snap_result['error']}"
                )

        except Exception as e:
            error_msg = f"❌ Error capturing code change: {str(e)}"
            LOGGER.error(error_msg)
            return error_msg

    def get_recent_history(self, project_path: str = None) -> str:
        """
        Get recent mem history

        Returns:
            Recent history from mem command
        """
        try:
            history_result = run_mem_command(["history"], project_path or self.project_path)
            if history_result["success"]:
                return f"📚 Recent captures via mem:\n{history_result['output']}"
            else:
                return f"❌ Failed to get history: {history_result['error']}"
        except Exception as e:
            return f"❌ Error getting history: {str(e)}"


# Global instance for easy access
_agent_capture_instance = None


def get_agent_capture(project_path: str = None) -> AgentCapture:
    """
    Get global agent capture instance

    Args:
        project_path: Project path (if different from current)

    Returns:
        AgentCapture instance
    """
    global _agent_capture_instance

    if _agent_capture_instance is None or (
        project_path and project_path != _agent_capture_instance.project_path
    ):
        _agent_capture_instance = AgentCapture(project_path)

    return _agent_capture_instance


# Simple helper functions for easy integration
def capture_plan(plan_type: str, content: str, project_path: str = None) -> str:
    """
    Simple function to capture a plan/thinking step using mem
    """
    try:
        capture = get_agent_capture(project_path)
        return capture.capture_auto_plan(plan_type, content, project_path)
    except Exception as e:
        error_msg = f"Failed to capture plan: {e}"
        LOGGER.error(error_msg)
        return error_msg


def capture_change(
    change_type: str, file_path: str, description: str = None, project_path: str = None
) -> str:
    """
    Simple function to capture a code change using mem
    """
    try:
        capture = get_agent_capture(project_path)
        return capture.capture_code_change(change_type, file_path, description, project_path)
    except Exception as e:
        error_msg = f"Failed to capture change: {e}"
        LOGGER.error(error_msg)
        return error_msg
