# Desktop reset — 2026-11-02

Josh sends the desktop a link to this file. Read it top to bottom and do the steps in order.

Hi — this is Josh. We're resetting how you understand LiveReact. Your old picture of the project and the
agents is out of date. From now on the repo is the only source of truth, not your memory. Please do this in order:

1. HANDS OFF WHILE THE NIGHT RUNS. Check the task first:
   powershell -Command "(Get-ScheduledTask -TaskName 'LiveReact nightly search').State"
   If it says Running, stop here and tell me. Do not run git, edit files or start Python in the repo
   until it says Ready.

2. CONFIRM TODAY'S RUN GOT THE NEW CODE. In today's log under results\nightly\ (or the agent log), find the
   line "self-update: pulled harness <old> -> <new>" and tell me the <new> hash. Today's 2 PM run should show
   d1c27f7 (it started before the later pushes). Also list today's agent branches and whether each was
   ACCEPTED or REJECTED by its gate. Don't merge anything yet.

3. GET CURRENT. In the LiveReact folder:
   git status            (must be clean; if not, tell me what's there and don't touch it)
   git checkout harness
   git pull --ff-only origin harness
   git log -1 --oneline  (should be the commit that added docs\DESKTOP_RESET_20261102.md, after 7d71de6 "Merge the Mac's D-31..D-42 show work and the re-aimed agents ..." or newer)

4. CLEAR YOUR OLD LIVEREACT MEMORY (don't delete it, set it aside):
   - List your memory folder for this project (under %USERPROFILE%\.claude\projects\ , the folder whose name
     matches the LiveReact path, subfolder memory\).
   - Rename that memory folder to memory_old_20261102. Tell me which files were in it.
   - Don't write any new memories about the agents' line-up, their jobs or the project's goal. Those live
     in the repo and change; a remembered copy goes stale. The only thing worth remembering is
     "read CLAUDE.md in the repo first".

5. RE-READ, THEN TELL ME YOUR JOB. Read CLAUDE.md (new today), the header of
   tools\windows\nightly_agent.ps1 including its $Allow* path lists, and docs\AGENT_BRIEF.md,
   docs\AGENT_BRIEF_UI.md, docs\AGENT_BRIEF_APP.md. Then reply in under 15 lines:
   - what this desktop is for, and what it must never do
   - each nightly agent: its brief file, its job, and what it may change
   - what the App agent's first priority is now (it changed today)
   - anything in those files that contradicts something else in them
   Quote the files. Don't add anything from memory.

What changed today, so you know what you're reading:
- CLAUDE.md is new. It's the orientation file, and it says this desktop only runs the nightly agents.
- The agents were re-aimed (this was decided on 10-30, but it only reached GitHub today): the User-Tester
  (docs\AGENT_BRIEF_UI.md), the App agent (docs\AGENT_BRIEF_APP.md), and the Preference Verifier
  (docs\AGENT_BRIEF.md). "Bugs & UI" and "Track Classifier" are their old names. Forget those descriptions.
- The App agent's first priority is now show-night resilience (find failures nobody has hit yet).
- Decision D-42 in docs\13_DECISION_LOG.md: Josh chose D-33's roll rule over the 11-01 planner commit made
  on this desktop. Don't re-litigate it.
- From now on, code changes come from the Mac, not from sessions on this desktop.

Today's 2 PM agents started from older code (d1c27f7). When Josh asks you to merge their branches, expect
conflicts with 7d71de6. Report them to Josh; don't resolve planner or engine conflicts by yourself.
