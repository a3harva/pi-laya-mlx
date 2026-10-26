/**
 * Laya guard - screens every tool call with the Laya decision model running locally on MLX.
 *
 * Before a tool runs, the call (tool name, arguments, cwd) goes to the laya-guard server, which asks
 * Laya which risk category the call falls in. Calls judged risky are blocked, and the reason goes
 * back to the model as the tool result so it can pick a safer approach.
 *
 * Environment:
 *   LAYA_GUARD_URL        server URL (default http://127.0.0.1:8723)
 *   LAYA_GUARD_THRESHOLD  block when risk >= this (default: the server's threshold)
 *   LAYA_GUARD_FAIL       "closed" (default) blocks calls when the server is unreachable; "open" allows them
 *   LAYA_GUARD_SKIP       comma-separated tool names that are never screened (default: none)
 *   LAYA_GUARD_CONFIRM    "1" asks the user to allow a blocked call when a UI is available
 *   LAYA_GUARD_AUTOSTART  "0" disables starting the bundled server when it is not running
 *   LAYA_GUARD_API_KEY    bearer token, when the server requires one
 *   LAYA_GUARD_TIMEOUT_MS how long one check may take before it counts as unreachable (default 15000)
 *
 * Usage: pi -e ./extension    Commands: /laya (status), /laya on, /laya off
 */

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, openSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const URL_BASE = (process.env.LAYA_GUARD_URL ?? "http://127.0.0.1:8723").replace(/\/$/, "");
const THRESHOLD = process.env.LAYA_GUARD_THRESHOLD ? Number(process.env.LAYA_GUARD_THRESHOLD) : undefined;
const FAIL_OPEN = process.env.LAYA_GUARD_FAIL === "open";
const SKIP = new Set((process.env.LAYA_GUARD_SKIP ?? "").split(",").map((name) => name.trim()).filter(Boolean));
const CONFIRM = process.env.LAYA_GUARD_CONFIRM === "1";
const AUTOSTART = process.env.LAYA_GUARD_AUTOSTART !== "0";
const API_KEY = process.env.LAYA_GUARD_API_KEY;

const CHECK_TIMEOUT_MS = Number(process.env.LAYA_GUARD_TIMEOUT_MS ?? 15_000);
const STARTUP_TIMEOUT_MS = 60_000;
const SERVER_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "server");
const LOG_DIR = join(homedir(), ".cache", "laya-guard");

interface GuardVerdict {
	decision: "allow" | "block";
	risk: number;
	threshold: number;
	category: string;
	probabilities: Record<string, number>;
	reason?: string;
	latency_ms: number;
}

async function request<T>(path: string, body?: unknown, timeoutMs = CHECK_TIMEOUT_MS): Promise<T> {
	const response = await fetch(`${URL_BASE}${path}`, {
		method: body === undefined ? "GET" : "POST",
		headers: {
			"content-type": "application/json",
			...(API_KEY ? { authorization: `Bearer ${API_KEY}` } : {}),
		},
		body: body === undefined ? undefined : JSON.stringify(body),
		signal: AbortSignal.timeout(timeoutMs),
	});
	if (!response.ok) throw new Error(`laya-guard ${path}: HTTP ${response.status} ${await response.text()}`);
	return (await response.json()) as T;
}

async function healthy(): Promise<boolean> {
	try {
		await request("/health", undefined, 2_000);
		return true;
	} catch {
		return false;
	}
}

/** Start the bundled server as a detached daemon, so other pi sessions share it. */
function startServer(): boolean {
	const binary = join(SERVER_DIR, ".venv", "bin", "laya-guard-serve");
	if (!existsSync(binary)) return false;
	mkdirSync(LOG_DIR, { recursive: true });
	const log = openSync(join(LOG_DIR, "server.log"), "a");
	const port = new URL(URL_BASE).port || "8723";
	const child = spawn(binary, ["--port", port], { detached: true, stdio: ["ignore", log, log], env: process.env });
	child.unref();
	return true;
}

export default function (pi: ExtensionAPI) {
	let enabled = true;
	let ready: Promise<boolean> | undefined;
	const stats = { allowed: 0, blocked: 0, errors: 0 };

	/** Resolves once the server answers; starts it on first use when allowed. */
	function ensureServer(): Promise<boolean> {
		ready ??= (async () => {
			if (await healthy()) return true;
			if (!AUTOSTART || !startServer()) return false;
			const deadline = Date.now() + STARTUP_TIMEOUT_MS;
			while (Date.now() < deadline) {
				await new Promise((resolve) => setTimeout(resolve, 500));
				if (await healthy()) return true;
			}
			return false;
		})();
		return ready.then((ok) => {
			if (!ok) ready = undefined; // retry on the next call
			return ok;
		});
	}

	function showStatus(ctx: ExtensionContext) {
		const text = enabled ? `laya ✓${stats.allowed} ✗${stats.blocked}${stats.errors ? ` !${stats.errors}` : ""}` : "laya off";
		ctx.ui.setStatus("laya-guard", text);
	}

	pi.on("session_start", async (_event, ctx) => {
		showStatus(ctx);
		// Warm up in the background so the first tool call does not wait for the model to load.
		void ensureServer();
	});

	pi.on("tool_call", async (event, ctx) => {
		if (!enabled || SKIP.has(event.toolName)) return undefined;

		let verdict: GuardVerdict;
		try {
			if (!(await ensureServer())) throw new Error(`server not reachable at ${URL_BASE}`);
			verdict = await request<GuardVerdict>("/v1/guard", {
				tool: event.toolName,
				input: event.input,
				cwd: ctx.cwd,
				threshold: THRESHOLD,
			});
		} catch (error) {
			stats.errors++;
			showStatus(ctx);
			const message = error instanceof Error ? error.message : String(error);
			if (FAIL_OPEN) return undefined;
			return {
				block: true,
				reason: `Blocked: the Laya safety guard could not check this call (${message}). Tool calls stay blocked until the guard is reachable; tell the user.`,
			};
		}

		pi.appendEntry("laya-guard", {
			toolCallId: event.toolCallId,
			tool: event.toolName,
			decision: verdict.decision,
			risk: verdict.risk,
			category: verdict.category,
			latencyMs: verdict.latency_ms,
		});

		if (verdict.decision === "allow") {
			stats.allowed++;
			showStatus(ctx);
			return undefined;
		}

		if (CONFIRM && ctx.hasUI) {
			const choice = await ctx.ui.select(
				`Laya flagged ${event.toolName} as '${verdict.category}' (risk ${verdict.risk.toFixed(2)}):\n\n${preview(event.input)}\n\nAllow it?`,
				["Block", "Allow once"],
			);
			if (choice === "Allow once") {
				stats.allowed++;
				showStatus(ctx);
				return undefined;
			}
		}

		stats.blocked++;
		showStatus(ctx);
		ctx.ui.notify(`Laya blocked ${event.toolName} (${verdict.category}, risk ${verdict.risk.toFixed(2)})`, "warning");
		return { block: true, reason: verdict.reason ?? "Blocked by the Laya safety guard." };
	});

	pi.registerCommand("laya", {
		description: "Laya guard status; /laya on or /laya off toggles screening",
		handler: async (args, ctx) => {
			const arg = args.trim();
			if (arg === "on" || arg === "off") enabled = arg === "on";
			showStatus(ctx);
			const up = await healthy();
			ctx.ui.notify(
				`Laya guard ${enabled ? "on" : "off"} · server ${up ? "up" : "down"} at ${URL_BASE} · allowed ${stats.allowed}, blocked ${stats.blocked}, errors ${stats.errors}`,
				"info",
			);
		},
	});
}

function preview(input: Record<string, unknown>): string {
	const text = typeof input.command === "string" ? input.command : JSON.stringify(input, null, 2);
	return text.length > 400 ? `${text.slice(0, 400)}…` : text;
}
