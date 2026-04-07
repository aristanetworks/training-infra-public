# Obsidian Documentation Skill — Design Spec

**Date:** 2026-04-07
**Status:** Approved
**Skill name:** `obsidian-doc`
**Location:** `~/.claude/skills/obsidian-doc`

## Purpose

A single Claude Code skill that handles all Obsidian vault documentation workflows: bug investigations, code references, project updates, planning notes, and architecture documentation. The skill auto-detects the document type from conversation context, selects the correct template and destination folder, writes substantive content (not empty templates), invokes the documentation-agent for mermaid diagrams and function references, and links the document to the relevant project index.

The goal is long-term knowledge preservation — anyone should be able to open the Obsidian vault a year from now and understand exactly what was done, why, and how.

## Vault Location

Read from the user's CLAUDE.md files. Currently: `/Users/rparkin/personal/Home/`. The skill does not hardcode this path; it looks for the Obsidian vault instructions in CLAUDE.md.

## Document Type Detection

The skill examines conversation context (branch name, commit messages, recent tool calls, discussion topic) to classify the work into one of four types:

| Type | Trigger Signals | Template | Destination |
|------|----------------|----------|-------------|
| `troubleshooting` | Bug fix commit, debugging session, branch starts with `bug/` or `fix/`, systematic-debugging skill was used | Bug Investigation Template | `Work/Bug-investigations/` |
| `code-reference` | Feature implemented, refactoring done, new module created, branch starts with `feature/` or `improvement/` | New Document Template | `Work/Projects/<project>/` |
| `documentation` | Docs updated, architecture decisions, how-to knowledge captured | New Document Template | `Work/Projects/<project>/` |
| `planning` | Design spec written, feature planned, roadmap discussion, brainstorming skill was used | New Document Template | `Work/Projects/<project>/` |

Detection priority: branch name patterns first, then commit message keywords, then conversation context. If ambiguous, the skill asks the user.

## Project Matching

The skill scans `Work/Projects/` for existing project folders and matches using:

1. Repo name and CLAUDE.md project references (e.g., "ATL Platform", "Lab Infrastructure")
2. Tags in the conversation or commit messages
3. Existing project index frontmatter tags

If no match is found:
1. Ask the user which project this belongs to, presenting existing projects as options
2. Offer to create a new project folder and index using the New Project Template
3. If creating, pre-fill the index objectives from conversation context

## Frontmatter Schema

### All document types

```yaml
---
project: "<matching project name>"
type: "<detected type>"
status: "<default by type>"
tags:
  - work
  - <project-relevant tags from project index>
date: <today YYYY-MM-DD>
---
```

### Additional fields for `troubleshooting`

```yaml
branch: "<current git branch>"
commit: "<latest commit hash>"
severity: "<high|medium|low, inferred from context>"
```

**Status defaults:**
- `troubleshooting` → "Complete" (investigation is finished)
- All others → "Active"

**Tag handling:** Reuse existing tags from the vault (listed in CLAUDE.md). Pull relevant tags from the project index frontmatter. Only create new tags if truly necessary.

## Content Generation

The skill writes substantive content drawn from the conversation, not empty template placeholders. Each section is populated with real knowledge.

### Content by type

**troubleshooting:**
- Summary of the bug
- Symptoms observed
- Environment details (branch, files, component)
- Root cause analysis with code examples (before/after)
- Fix details with code snippets
- Investigation method (step-by-step how root cause was found)
- Files modified with one-line summaries
- Testing checklist
- Mermaid diagrams (architecture, data flow, or sequence as appropriate)

**code-reference:**
- Overview of what was built/changed and why
- Architecture description with mermaid diagrams
- Function/method reference table (from documentation-agent)
- Key design decisions and alternatives considered
- Files created/modified with summaries
- Usage examples
- Dependencies and integration points

**documentation:**
- Topic overview
- Detailed explanation with diagrams
- Related components and how they connect
- References and links

**planning:**
- Goals and objectives
- Proposed approach with mermaid architecture diagram
- Key decisions and trade-offs
- Implementation phases or steps
- Success criteria

### What gets captured for long-term understanding

Every document includes:
- **Why** the change was made, not just what
- **Architecture diagrams** (mermaid) showing how components relate
- **Before/after code examples** for fixes and refactors
- **Decision rationale** — alternatives considered and why this approach won
- **File manifest** — every file touched with a one-line summary

## Documentation Agent Integration

After writing the initial Obsidian document, the skill invokes the `documentation-agent` (subagent) to generate:

1. **Mermaid diagrams** — architecture flowcharts, sequence diagrams, class diagrams as appropriate for the work done. Each diagram must have a title using the `---\ntitle: ...\n---` syntax.
2. **Function/method reference tables** — for any code written or modified, listing signatures, parameters, return values, and descriptions.

These are embedded directly in the Obsidian document body (not in separate files), keeping everything self-contained and readable.

The skill passes the documentation-agent:
- The list of files that were modified/created
- A summary of what was done
- Instructions to return mermaid blocks and reference tables only (no separate file creation)

## Project Index Linking

After creating the document, the skill updates the matching project index:

1. **Add wikilink** to the Related Links section:
   ```
   - [[Document Title]] — one-line summary (YYYY-MM-DD)
   ```

2. **Add dataview section** if the project index doesn't have one for this document type:
   ```markdown
   ## Bug Investigations

   ```dataview
   TABLE status AS "Status", severity AS "Severity", dateformat(date, "yyyy-MM-dd") AS "Date"
   FROM "Work/Bug-investigations"
   WHERE project = "<project name>"
   SORT date DESC
   ```
   ```

   Dataview section names and query sources by type:
   - `troubleshooting` → "Bug Investigations" — queries `FROM "Work/Bug-investigations"` filtered by `project`
   - `code-reference` → "Code References" — queries `FROM "Work/Projects/<project>"` filtered by `type = "code-reference"`
   - `documentation` → "Documentation" — queries `FROM "Work/Projects/<project>"` filtered by `type = "documentation"`
   - `planning` → "Planning" — queries `FROM "Work/Projects/<project>"` filtered by `type = "planning"`

3. **New project creation** — if a new project folder was created, the index is populated using the New Project Template with:
   - Project name from context
   - Objectives pre-filled from conversation
   - Tags matching the repo/area
   - Related project links to any connected projects

## Wikilink Conventions

Follow existing Obsidian patterns:
- `[[Project Index]]` for project links
- Descriptive filenames with spaces (not kebab-case)
- Tags use existing vault tag vocabulary from CLAUDE.md

## Invocation

### Explicit
User types `/obsidian-doc` or asks "document this in Obsidian". Accepts an optional free-text argument to override the title: `/obsidian-doc "CVP session timeout investigation"`.

### Proactive
After completing significant work (bug fix, feature, refactor), the main assistant invokes the skill as part of wrapping up. This is the primary intended use.

### From other agents
The documentation-agent, code-reviewer, or other agents can trigger this skill after finishing their work.

## Boundaries

The skill does NOT:
- Generate standalone mermaid files in code directories (that's the documentation-agent's separate responsibility)
- Modify code files
- Commit to git (the Obsidian vault is not part of the code repo)
- Delete files from the Obsidian vault without explicit user request
- Create duplicate documents (checks for existing documents with the same title before creating)

## File Structure

```
~/.claude/skills/obsidian-doc          # Skill definition file

Obsidian vault structure:
~/personal/Home/Work/
  Templates/
    Bug Investigation Template.md       # Existing
    New Document Template.md            # Existing
    New Project Template.md             # Existing
  Bug-investigations/
    Terminal Tab Race Condition.md       # Example output
  Projects/
    ATL Platform/
      ATL Platform Index.md             # Updated with links + dataview
    Lab Infrastructure/
      Lab Infrastructure Index.md       # Updated with links + dataview
    <new projects created as needed>/
```

## Success Criteria

1. After any significant work session, invoking `/obsidian-doc` produces a complete, self-contained document with diagrams that someone can read a year later and fully understand what was done
2. Documents are correctly linked to project indexes with working dataview queries
3. No manual template filling — all content is substantive
4. Auto-detection correctly classifies document type in >90% of cases
5. Existing vault conventions (tags, frontmatter, wikilinks) are respected
