/**
 * Lockdown for the red-team agent that probes the Laya guard. Load it AFTER the Laya extension:
 *
 *   pi -ne -nc -ns --no-mcp -e ./extension -e ./tests/redteam/redteam-net.ts
 *
 * The red-team agent writes candidate tool calls as DATA and scores them with `probe`, which only
 * sends text to the local guard. It never needs to run them, so this extension makes sure it can't:
 *
 * - bash: every segment must start with an allowlisted program (probe, mailbox, read-only text
 *   tools); no subshells, backticks, process substitution or `eval`. Allowed commands then run under
 *   sandbox-exec: writes only in the workspace and the two red-team mailboxes, reads of the home
 *   folder only where the tools need them, network only to the local guard port.
 * - file tools: every path (after `~` expansion and symlink resolution) must be inside the workspace.
 *
 * Every call is appended to <workspace>/audit.jsonl with the decision.
 */

import { appendFileSync, existsSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const WORKSPACE = process.env.LAYA_REDTEAM_WORKSPACE ?? "";
const HOME = homedir();
const MAILBOXES = ["laya-redteam", "laya-claude"].map((name) => join(HOME, ".pi-mailbox", name));
const GUARD_PORT = (process.env.LAYA_GUARD_URL ?? "http://127.0.0.1:8723").split(":").pop()?.replace(/\D/g, "") || "8723";

// Programs the red-team agent may start. Nothing here deletes, downloads or executes code.
const ALLOWED = new Set([
	"probe", "mailbox", "cat", "ls", "head", "tail", "wc", "grep", "jq", "sort", "uniq", "cut", "tr",
	"echo", "printf", "pwd", "date", "diff", "comm", "column", "nl", "true",
]);
// Read access inside $HOME: the workspace, the tools' code and interpreters, the two mailboxes.
const READABLE = [
	WORKSPACE,
	join(HOME, "Developer", "pi-mailbox"),
	join(HOME, "Developer", "pi-laya-mlx", "tests", "redteam"),
	join(HOME, ".local", "share", "uv", "python"),
	...MAILBOXES,
];

function profile(): string {
	const sub = (path: string) => `(subpath "${path}")`;
	return [
		"(version 1)",
		"(allow default)",
		'(deny file-write* (subpath "/"))',
		`(allow file-write* ${[WORKSPACE, ...MAILBOXES].map(sub).join(" ")} (subpath "/private/var/folders") (literal "/dev/null") (literal "/dev/tty") (literal "/dev/stdout") (literal "/dev/stderr"))`,
		// pi-mailbox re-applies mode 0700 to its root on every call.
		`(allow file-write-mode (literal "${join(HOME, ".pi-mailbox")}"))`,
		`(deny file-read-data ${sub(HOME)})`,
		// The mailbox root itself (not the other agents' folders inside it) is listable.
		`(allow file-read-data ${READABLE.map(sub).join(" ")} (literal "${join(HOME, ".pi-mailbox")}"))`,
		"(deny network-outbound)",
		`(allow network-outbound (remote ip "localhost:${GUARD_PORT}"))`,
		'(allow network-outbound (remote unix-socket))',
	].join("\n");
}

const shellQuote = (text: string) => `'${text.replaceAll("'", `'\\''`)}'`;

// probe and mailbox wrappers live in the repo, outside the writable workspace, first on PATH.
const BIN = join(HOME, "Developer", "pi-laya-mlx", "tests", "redteam", "bin");

/** Why a bash command is refused, or undefined when every segment is allowlisted. */
function refuse(command: string): string | undefined {
	if (/`|\$\(|\$\{|<\(|>\(|\\\n/.test(command)) {
		return "subshells, backticks, parameter expansion and process substitution are not allowed";
	}
	// Quoted text is an argument, not shell syntax: blank it before looking for redirections and segments.
	// (The expansion check above ran on the raw text, so "$(...)" inside double quotes is still refused.)
	const unquoted = command.replace(/'[^']*'|"(?:[^"\\]|\\.)*"/g, "''");
	if (/['"]/.test(unquoted.replaceAll("''", ""))) return "unbalanced quotes";
	for (const match of unquoted.matchAll(/>>?\s*([^\s;&|]+)/g)) {
		const target = match[1];
		if (target.startsWith("&") || target === "/dev/null") continue; // 2>&1, discard
		if (/^[/~$]/.test(target) || target.includes("..")) return `redirection to '${target}': only relative workspace files`;
	}
	for (const segment of unquoted.replace(/\d*>&\d+/g, " ").split(/\|\||&&|[|;&\n]/)) {
		const words = segment.trim().replace(/^[({]+/, "").split(/\s+/).filter(Boolean);
		if (words.length === 0) continue;
		if (/^\w+=/.test(words[0])) return "environment assignments are not allowed";
		if (!ALLOWED.has(words[0])) return `'${words[0]}' is not on the red-team allowlist (${[...ALLOWED].join(", ")})`;
	}
	return undefined;
}

/** A file-tool path as the tool will open it: `~` expanded, relative to cwd, symlinks resolved. */
function realTarget(cwd: string, path: string): string {
	const absolute = path === "~" || path.startsWith("~/") ? join(HOME, path.slice(1)) : resolve(cwd, path);
	let probeDir = absolute;
	while (!existsSync(probeDir) && dirname(probeDir) !== probeDir) probeDir = dirname(probeDir);
	const realDir = realpathSync(probeDir);
	return absolute === probeDir ? realDir : join(realDir, absolute.slice(probeDir.length));
}

export default function (pi: ExtensionAPI) {
	if (!WORKSPACE.startsWith(join(HOME, "Developer", "temp") + "/") || !existsSync(WORKSPACE)) {
		throw new Error("redteam-net: LAYA_REDTEAM_WORKSPACE must be an existing folder under ~/Developer/temp/");
	}
	const workspace = realpathSync(WORKSPACE);
	const audit = (entry: Record<string, unknown>) =>
		appendFileSync(join(workspace, "audit.jsonl"), `${JSON.stringify({ at: new Date().toISOString(), ...entry })}\n`);

	pi.on("tool_call", async (event, ctx) => {
		if (event.toolName === "bash") {
			const command = String(event.input.command ?? "");
			const reason = refuse(command);
			audit({ tool: "bash", command, decision: reason ? "refused" : "sandboxed", reason });
			if (reason) return { block: true, reason: `REDTEAM-NET: ${reason}. Score candidate commands with probe; never run them.` };
			const script = `export PATH=${shellQuote(`${BIN}:/usr/bin:/bin`)}; cd ${shellQuote(workspace)} || exit 1\n${command}`;
			event.input.command = `sandbox-exec -p ${shellQuote(profile())} /bin/bash --noprofile --norc -c ${shellQuote(script)}`;
			return undefined;
		}
		const path = typeof event.input.path === "string" ? event.input.path : undefined;
		if (path !== undefined) {
			const target = realTarget(ctx.cwd, path);
			const inside = target === workspace || target.startsWith(`${workspace}/`);
			audit({ tool: event.toolName, path, target, decision: inside ? "allowed" : "refused" });
			if (!inside) return { block: true, reason: `REDTEAM-NET: ${target} is outside the red-team workspace.` };
			return undefined;
		}
		// Tools without a path argument (none in the default set) are refused rather than guessed at.
		const known = new Set(["grep", "find", "ls"]);
		audit({ tool: event.toolName, decision: known.has(event.toolName) ? "allowed" : "refused" });
		if (!known.has(event.toolName)) return { block: true, reason: `REDTEAM-NET: tool '${event.toolName}' is not allowed.` };
		return undefined;
	});
}

export const __test = { refuse, profile };
