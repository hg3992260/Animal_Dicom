// Minimal Node client for the SSD+VR Viewer control bridge. No dependencies.
//
//   node node_client.js query_state
//   node node_client.js set_mode mode=cinematic
//   node node_client.js set_camera azimuth=45 elevation=12
//   node node_client.js load_dicom '{"path":"D:\\case"}'      // JSON also works
//
// key=value shorthand is preferred from PowerShell/cmd because those shells
// strip the double quotes inside a JSON argument.
const net = require("net");
const fs = require("fs");
const path = require("path");

function bridgeFile() {
  if (process.env.SSD_VR_BRIDGE_FILE) return process.env.SSD_VR_BRIDGE_FILE;
  if (process.env.LOCALAPPDATA) return path.join(process.env.LOCALAPPDATA, "SSD_VR_MCP", "bridge.json");
  return path.join(require("os").homedir(), ".ssd_vr_mcp", "bridge.json");
}

function discover() {
  try {
    const info = JSON.parse(fs.readFileSync(bridgeFile(), "utf8"));
    if (info && info.port) return Number(info.port);
  } catch (e) { /* no discovery file: fall back to the default port */ }
  return Number(process.env.SSD_VR_MCP_PORT || 7799);
}

function coerce(v) {
  if (v === "true") return true;
  if (v === "false") return false;
  if (v === "null") return null;
  if (/^-?\d+$/.test(v)) return parseInt(v, 10);
  if (/^-?\d*\.\d+$/.test(v)) return parseFloat(v);
  if (/^\[.*\]$/.test(v)) { try { return JSON.parse(v); } catch (e) { return v; } }
  return v;
}

function parseArgs(spec) {
  if (!spec) return {};
  const s = String(spec).trim();
  if (s.startsWith("{")) return JSON.parse(s);
  const out = {};
  for (const tok of s.split(/\s+/)) {
    const i = tok.indexOf("=");
    if (i < 0) throw new Error(`argument '${tok}' is neither key=value nor JSON`);
    out[tok.slice(0, i)] = coerce(tok.slice(i + 1));
  }
  return out;
}

const op = process.argv[2] || "query_state";
let args = {};
try {
  args = parseArgs(process.argv.slice(3).join(" "));
} catch (e) {
  console.error(JSON.stringify({ ok: false, error: e.message }));
  process.exit(2);
}
const port = discover();

const sock = net.connect(port, "127.0.0.1", () => {
  sock.write(JSON.stringify({ id: "node-1", op, args }) + "\n");
});

let buf = "";
sock.on("data", (d) => {
  buf += d.toString("utf8");
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch (e) { continue; }
    if (msg.id === "node-1") {
      console.log(JSON.stringify({ port, ok: msg.ok, data: msg.data }, null, 2));
      sock.end();
      process.exit(msg.ok ? 0 : 1);
    } else {
      console.error("[event]", msg.type, JSON.stringify(msg.data).slice(0, 200));
    }
  }
});

sock.on("error", (e) => {
  console.error(JSON.stringify({ ok: false, error: e.message, port, bridge_file: bridgeFile() }));
  process.exit(1);
});
