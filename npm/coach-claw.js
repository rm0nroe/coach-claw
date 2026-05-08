#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const VERSION = require(path.join(ROOT, "package.json")).version;

function usage() {
  console.log(`Coach Claw ${VERSION}

Usage:
  coach-claw doctor
  coach-claw install [--seed | --no-seed] [--fresh] [--no-prune-backups]
  coach-claw launchd
  coach-claw config <set|preview|wizard> [...]
  coach-claw help

Examples:
  coach-claw doctor
  coach-claw install --seed
  coach-claw install --no-seed
  coach-claw launchd
  coach-claw config wizard
  coach-claw config preview
  coach-claw config set --theme ocean --statusline pips
  coach-claw config set --elo 1200 2800

The /config slash command inside Claude Code edits the same file.`);
}

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(1);
}

function warn(message) {
  console.error(`WARN: ${message}`);
}

function ok(message) {
  console.log(`OK: ${message}`);
}

function isSupportedPlatform() {
  return process.platform === "darwin" || process.platform === "linux";
}

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: ROOT,
    encoding: "utf8",
    stdio: options.stdio || "pipe",
    env: options.env || process.env
  });
}

function commandExists(command) {
  const result = run("sh", ["-c", `command -v ${quoteForShell(command)}`]);
  return result.status === 0;
}

function quoteForShell(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

function pythonVersion() {
  const probe = [
    "import sys",
    "print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
  ].join("; ");
  const result = run("python3", ["-c", probe]);
  if (result.status !== 0) {
    return null;
  }
  return result.stdout.trim();
}

function pythonIsAdequate(version) {
  if (!version) {
    return false;
  }
  const parts = version.split(".").map((part) => Number(part));
  if (parts.length < 2 || parts.some((part) => Number.isNaN(part))) {
    return false;
  }
  return parts[0] > 3 || (parts[0] === 3 && parts[1] >= 8);
}

function claudeDir() {
  return process.env.CLAUDE_DIR || path.join(os.homedir(), ".claude");
}

function ensureWritableClaudeDir() {
  const dir = claudeDir();
  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.accessSync(dir, fs.constants.W_OK);
    return true;
  } catch (error) {
    return false;
  }
}

function doctor({ fatal = false } = {}) {
  let failures = 0;
  const recordFailure = (message) => {
    failures += 1;
    if (fatal) {
      fail(message);
    }
    console.error(`FAIL: ${message}`);
  };

  if (isSupportedPlatform()) {
    ok(`supported OS: ${process.platform}`);
  } else {
    recordFailure("Coach Claw supports macOS/Linux only.");
  }

  if (commandExists("bash")) {
    ok("bash available");
  } else {
    recordFailure("bash not found in PATH.");
  }

  if (commandExists("git")) {
    ok("git available");
  } else {
    recordFailure("git not found in PATH.");
  }

  if (commandExists("python3")) {
    const version = pythonVersion();
    if (pythonIsAdequate(version)) {
      ok(`python3 ${version} available`);
    } else {
      recordFailure("Coach Claw needs python3 >= 3.8. Install Python 3, then retry.");
    }
  } else {
    recordFailure("Coach Claw needs python3 >= 3.8. Install Python 3, then retry.");
  }

  if (ensureWritableClaudeDir()) {
    ok(`Claude dir writable: ${claudeDir()}`);
  } else {
    recordFailure(`Claude dir is not writable: ${claudeDir()}`);
  }

  if (commandExists("claude")) {
    ok("claude CLI available for weekly /coach-insights refresh");
  } else {
    warn("claude CLI not found; install still works, but weekly /coach-insights refresh needs Claude Code on PATH.");
  }

  const installScript = path.join(ROOT, "install.sh");
  if (fs.existsSync(installScript)) {
    ok("install.sh present");
  } else {
    recordFailure("package is missing install.sh.");
  }

  const liveCoach = path.join(claudeDir(), "coach");
  const liveSessionHook = path.join(claudeDir(), "hooks", "coach-session-start.py");
  const livePromptHook = path.join(claudeDir(), "hooks", "coach-user-prompt.py");
  if (fs.existsSync(liveCoach)) {
    ok(`installed coach dir present: ${liveCoach}`);
  } else {
    warn("Coach is not installed yet; run `coach-claw install --seed`.");
  }
  if (fs.existsSync(liveSessionHook) && fs.existsSync(livePromptHook)) {
    ok("installed hooks present");
  } else {
    warn("installed hooks not found yet; run `coach-claw install --seed`.");
  }

  if (failures > 0) {
    process.exit(1);
  }
}

function runInstall(args) {
  doctor({ fatal: true });
  const script = path.join(ROOT, "install.sh");
  const result = run("bash", [script, ...args], { stdio: "inherit" });
  process.exit(result.status === null ? 1 : result.status);
}

function runLaunchd() {
  if (process.platform !== "darwin") {
    fail(
      "launchd is macOS-only. On Linux, add this cron entry: " +
      "0 4 * * * $HOME/.claude/coach/bin/insights.sh 1d >> /tmp/claude-coach.log 2>&1"
    );
  }
  doctor({ fatal: true });
  const script = path.join(ROOT, "install-launchd.sh");
  const result = run("bash", [script], { stdio: "inherit" });
  process.exit(result.status === null ? 1 : result.status);
}

function runConfig(args) {
  // configure.py lives in the LIVE install (claudeDir()/coach/bin/), not
  // the npm package — the npx cache is read-only and the script needs
  // to import its sibling modules (themes, statusline_variants,
  // user_config) which only exist in the install target.
  const script = path.join(claudeDir(), "coach", "bin", "configure.py");
  if (!fs.existsSync(script)) {
    fail(
      "coach-claw config: Coach Claw isn't installed yet. " +
      "Run: npx @rm0nroe/coach-claw@latest install"
    );
  }
  // If the user has CLAUDE_DIR set (custom install location), propagate
  // COACH_CONFIG_DIR so user_config.py writes to the matching coach dir
  // instead of falling back to ~/.claude/coach.
  const env = { ...process.env };
  if (process.env.CLAUDE_DIR) {
    env.COACH_CONFIG_DIR = path.join(process.env.CLAUDE_DIR, "coach");
  }
  const result = spawnSync("python3", [script, ...args], {
    stdio: "inherit",
    env,
  });
  process.exit(result.status === null ? 1 : result.status);
}

const [command, ...args] = process.argv.slice(2);

switch (command || "help") {
  case "doctor":
    doctor();
    break;
  case "install":
    runInstall(args);
    break;
  case "launchd":
    runLaunchd();
    break;
  case "config":
    runConfig(args);
    break;
  case "--version":
  case "-v":
  case "version":
    console.log(VERSION);
    break;
  case "--help":
  case "-h":
  case "help":
    usage();
    break;
  default:
    usage();
    fail(`unknown command: ${command}`);
}
