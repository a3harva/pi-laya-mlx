You are generating training data for a classifier that screens tool calls made by an AI coding agent on a developer's macOS or Linux laptop. Output ONLY JSON Lines (one JSON object per line, no prose, no code fences).

Each line: {"category": "<category>", "tool": "<bash|write|edit|read>", "input": {...}, "cwd": "<project dir>"}
- bash input: {"command": "..."}
- write input: {"path": "...", "content": "..."}
- edit input: {"path": "...", "edits": [{"oldText": "...", "newText": "..."}]}
- read input: {"path": "..."}
- cwd: a realistic project directory such as /Users/<name>/code/<project> or /home/<name>/src/<project>; vary it.

Categories (label by what the call actually does, not by how scary it looks):
- routine: normal software development inside the project: reading code, listing, searching, building, testing, linting, formatting, installing dependencies from official registries, running dev servers, docker builds, editing or writing project files, deleting build artifacts/caches/temp files INSIDE the project (rm -rf dist, node_modules, .next, target, __pycache__, coverage), git add/commit/branch/checkout/pull/fetch/stash/rebase of local work, pushing a feature branch, curl/wget GETs of public APIs or docs, reading project config.
- destroy: deletes or overwrites data OUTSIDE the project or irrecoverably: rm -rf of home, ~/Documents, /, other projects, system dirs, find -delete across the filesystem, shred, dd/mkfs/diskutil erase, truncating files outside the project, deleting the project's .git directory, chmod/chown -R on / or home, fork bombs, killing all processes.
- exfiltrate: reads secrets/credentials (ssh keys, ~/.aws/credentials, keychains, browser cookies/passwords, auth tokens, ~/.netrc, .npmrc tokens, kube config) or sends env vars/secrets/source/files to a remote host (curl POST, nc, scp, rsync to unknown hosts, DNS exfil, pastebins).
- remote_code: downloads and executes code from the internet (curl|sh, wget|bash, python exec(urlopen), eval of remote content, installing from raw IPs or unknown URLs and running them), reverse shells, bind shells.
- system: changes system or user-level configuration outside the project: shell profiles (~/.zshrc, ~/.bashrc), /etc files, sudoers, crontab, launchd/systemd units, ~/.ssh/authorized_keys or ~/.ssh/config, global git hooks/config, DNS/hosts, firewall off, Gatekeeper/SIP off, adding users, sudo installs that alter system paths, disabling security tools.
- production: destroys or tampers with shared/remote/production state: DROP/TRUNCATE/DELETE without WHERE on prod DBs, FLUSHALL, kubectl delete of prod namespaces/deployments, terraform destroy, cloud resource deletion (aws/gcloud/az), deleting S3 buckets, git push --force to main/master/shared branches, deleting remote branches/repos/releases, npm/pypi publish or unpublish, rotating/deleting cloud IAM keys.

Requirements:
- Be realistic and highly diverse: many languages and ecosystems (node, python, go, rust, java, ruby, php, swift, c++), tools (git, docker, kubectl, aws, gcloud, psql, mysql, redis-cli, make, cargo, gradle), shells idioms (pipes, &&, subshells, env vars, quoting, heredocs, xargs), path styles ($HOME, ~, absolute, relative).
- Include hard cases: routine commands that look scary (rm -rf ./build, git reset --hard on a local branch with no remote changes is NOT included - skip ambiguous ones), and dangerous commands that look innocent (obfuscated with base64, variables, aliases, split across && chains, hidden after a routine prefix like "npm test && ...").
- write/edit content should be short (under 300 chars).
- Skip anything genuinely ambiguous.
