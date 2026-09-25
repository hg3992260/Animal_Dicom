# Minimal PowerShell client for the SSD+VR Viewer control bridge.
# No dependencies; works on Windows PowerShell 5.1 and PowerShell 7.
#
# IMPORTANT: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less
# script files as ANSI (cp936 on a Chinese system), so non-ASCII comments can
# swallow the next line and break parsing. Use UTF-8 WITH BOM if you must add
# non-ASCII text.
#
#   .\powershell_client.ps1 query_state
#   .\powershell_client.ps1 set_mode '{"mode":"cinematic"}'
param(
  [string]$Op = "query_state",
  [string]$ArgsJson = "{}"
)

$ErrorActionPreference = "Stop"

# 1) find the port: bridge discovery file first, else 7799
$bridgeFile = if ($env:SSD_VR_BRIDGE_FILE) { $env:SSD_VR_BRIDGE_FILE }
              else { Join-Path $env:LOCALAPPDATA "SSD_VR_MCP\bridge.json" }
$port = 7799
if (Test-Path $bridgeFile) {
  try { $port = [int](Get-Content $bridgeFile -Raw | ConvertFrom-Json).port } catch { }
}

# 2) connect and send one line of JSON
$client = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $port)
$stream = $client.GetStream()
$payload = (@{ id = "ps-1"; op = $Op; args = ($ArgsJson | ConvertFrom-Json) } | ConvertTo-Json -Compress) + "`n"
Write-Host "[ps] port=$port op=$Op" -ForegroundColor DarkGray
$bytes = [Text.Encoding]::UTF8.GetBytes($payload)
$stream.Write($bytes, 0, $bytes.Length)
$stream.Flush()

# 3) read lines; skip events ({"type":...}) until our own id comes back
$buffer = [byte[]]::new(65536)
$acc = ""
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
  $stream.ReadTimeout = 2000
  try { $n = $stream.Read($buffer, 0, $buffer.Length) } catch { continue }
  if ($n -le 0) { break }
  $acc += [Text.Encoding]::UTF8.GetString($buffer, 0, $n)
  while ($acc.Contains("`n")) {
    $idx = $acc.IndexOf("`n")
    $line = $acc.Substring(0, $idx).Trim()
    $acc = $acc.Substring($idx + 1)
    if (-not $line) { continue }
    $msg = $line | ConvertFrom-Json
    if ($msg.id -eq "ps-1") {
      [pscustomobject]@{ port = $port; ok = $msg.ok; data = $msg.data } | ConvertTo-Json -Depth 6
      $client.Close()
      exit $(if ($msg.ok) { 0 } else { 1 })
    }
    Write-Host "[event] $($msg.type)" -ForegroundColor DarkYellow
  }
}
$client.Close()
Write-Host '{"ok":false,"error":"timeout"}'
exit 1
