/**
 * Test-only safety net for live agent runs. Load it AFTER the Laya extension:
 *
 *   pi -ne -e ./extension -e ./tests/live/safety-net.ts
 *
 * Whatever Laya lets through still cannot touch real files:
 * - every bash command runs under macOS sandbox-exec, which denies writes outside LAYA_TEST_SANDBOX
 *   (and the system temp dir) and denies outbound network;
 * - write/edit calls whose path resolves outside LAYA_TEST_SANDBOX are blocked.
 * Calls that reach this extension (Laya allowed them) are appended to LAYA_TEST_LOG as JSONL.
 */

import { appendFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const SANDBOX = process.env.LAYA_TEST_SANDBOX;
const LOG = process.env.LAYA_TEST_LOG;
const SAFE_ROOT = process.env.LAYA_TEST_SAFE_ROOT ?? join(homedir(), "Developer", "temp");

function profile(root: string): string {
	return [
		"(version 1)",
		"(allow default)",
		'(deny file-write* (subpath "/"))',
		`(allow file-write* (subpath "${root}") (subpath "/private/var/folders") (subpath "/private/tmp/laya-guard-test")`,
		'  (literal "/dev/null") (literal "/dev/tty") (literal "/dev/stdout") (literal "/dev/stderr") (literal "/dev/dtracehelper"))',
		"(deny network-outbound (remote ip))",
		// Secrets must never reach the model provider, even if Laya misses a read.
		`(deny file-read* ${SECRET_DIRS.map((dir) => `(subpath "${join(homedir(), dir)}")`).join(" ")})`,
	].join("\n");
}

const SECRET_DIRS = [".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".pi/agent", ".netrc", ".npmrc", ".git-credentials", "Library/Keychains"];

/** Path as the tool will see it: `~` expanded, relative paths against cwd. */
function toolPath(cwd: string, path: string): string {
	if (path === "~" || path.startsWith("~/")) return join(homedir(), path.slice(1));
	return resolve(cwd, path);
}

const shellQuote = (text: string) => `'${text.replaceAll("'", `'\\''`)}'`;

export default function (pi: ExtensionAPI) {
	if (!SANDBOX || !SANDBOX.startsWith(`${SAFE_ROOT}/`)) {
		throw new Error(`safety-net: LAYA_TEST_SANDBOX must be a folder under ${SAFE_ROOT}/`);
	}

	pi.on("tool_call", async (event, ctx) => {
		if (LOG) appendFileSync(LOG, `${JSON.stringify({ reached: event.toolName, input: event.input })}\n`);

		if (event.toolName === "bash") {
			const command = String(event.input.command ?? "");
			event.input.command = `sandbox-exec -p ${shellQuote(profile(SANDBOX))} /bin/bash -c ${shellQuote(command)}`;
			return undefined;
		}
		// Every other built-in file tool (write, edit, read, grep, find, ls) stays inside the sandbox.
		if (typeof event.input.path === "string") {
			const path = toolPath(ctx.cwd, event.input.path);
			if (path !== SANDBOX && !path.startsWith(`${SANDBOX}/`)) {
				return { block: true, reason: `SAFETY-NET (test harness, not Laya): ${path} is outside the test sandbox.` };
			}
		}
		return undefined;
	});
}
