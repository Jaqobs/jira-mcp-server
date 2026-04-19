import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import jira_client

load_dotenv()

app = Server("jira-mcp-server")

REQUIRED_ENV_VARS = ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"]


def _check_env() -> list[str]:
    return [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="jira_get_active_issues",
            description=(
                "Fetch Jira issues in the configured active statuses (e.g. To Do, In Progress) "
                "for the project. Returns key, summary, status, assignee, priority, type, and URL."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="jira_get_issue_detail",
            description=(
                "Fetch full detail for a single Jira issue including description and comments. "
                "Use this to understand what work a ticket requires before starting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key, e.g. PROJ-123",
                    }
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_get_commit_prefix",
            description=(
                "Return the conventional commit prefix for a Jira issue key so it can be "
                "included in a git commit message, e.g. 'PROJ-123: '. "
                "Always call this before composing a commit message when working on a Jira ticket."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key, e.g. PROJ-123",
                    }
                },
                "required": ["issue_key"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    missing = _check_env()
    if missing:
        return [TextContent(
            type="text",
            text=(
                f"Missing required environment variables: {', '.join(missing)}.\n"
                f"Copy .env.example to .env and fill in the values."
            ),
        )]

    if name == "jira_get_active_issues":
        issues = await jira_client.get_active_issues()
        if not issues:
            return [TextContent(type="text", text="No active issues found.")]
        lines = [f"Found {len(issues)} active issue(s):\n"]
        for issue in issues:
            assignee = issue["assignee"] or "Unassigned"
            lines.append(
                f"• [{issue['key']}] {issue['summary']}\n"
                f"  Status: {issue['status']} | Type: {issue['issue_type']} | "
                f"Priority: {issue['priority']} | Assignee: {assignee}\n"
                f"  Updated: {issue['updated_iso']}\n"
                f"  URL: {issue['url']}\n"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "jira_get_issue_detail":
        issue_key = arguments.get("issue_key", "").strip().upper()
        if not issue_key:
            return [TextContent(type="text", text="issue_key is required.")]
        detail = await jira_client.get_issue_detail(issue_key)
        assignee = detail["assignee"] or "Unassigned"
        lines = [
            f"**{detail['key']}: {detail['summary']}**",
            f"Status: {detail['status']} | Type: {detail['issue_type']} | Priority: {detail['priority']}",
            f"Assignee: {assignee}",
            f"Created: {detail['created_iso']} | Updated: {detail['updated_iso']}",
            f"URL: {detail['url']}",
            "",
            "**Description:**",
            detail["description"] or "(no description)",
        ]
        if detail["comments"]:
            lines.append(f"\n**Comments ({len(detail['comments'])}):**")
            for c in detail["comments"]:
                lines.append(f"\n[{c['created_iso']}] {c['author']}:\n{c['body']}")
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "jira_get_commit_prefix":
        issue_key = arguments.get("issue_key", "").strip().upper()
        if not issue_key:
            return [TextContent(type="text", text="issue_key is required.")]
        return [TextContent(type="text", text=f"{issue_key}: ")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
