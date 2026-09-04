# Reads backend/.env and prints one of:
#   OK      -> TG_API_ID is a positive integer and TG_API_HASH is non-empty
#   BADID   -> TG_API_ID missing / empty / not a positive integer
#   BADHASH -> TG_API_ID ok but TG_API_HASH empty
#   BADPASSWORD -> APP_PASSWORD is weak, oversized, or still the placeholder
#   BADSECRET -> SESSION_SECRET is outside the required 48-1024 character range
# Exit code 0 for OK, 1 otherwise.
param([string]$EnvPath)
try {
    $k = Get-Content -Raw -LiteralPath $EnvPath -ErrorAction Stop
} catch {
    Write-Output 'BADID'
    exit 1
}
$m  = [regex]::Match($k, '(?im)^\s*TG_API_ID\s*=\s*([^\r\n]+)')
$id = if ($m.Success) { $m.Groups[1].Value.Trim() } else { '' }
$m2  = [regex]::Match($k, '(?im)^\s*TG_API_HASH\s*=\s*([^\r\n]+)')
$h = if ($m2.Success) { $m2.Groups[1].Value.Trim() } else { '' }
$mp = [regex]::Match($k, '(?im)^\s*APP_PASSWORD\s*=\s*([^\r\n]*)')
$password = if ($mp.Success) { $mp.Groups[1].Value } else { '' }
$ms = [regex]::Match($k, '(?im)^\s*SESSION_SECRET\s*=\s*([^\r\n]*)')
$secret = if ($ms.Success) { $ms.Groups[1].Value } else { '' }
$n = 0
[void][int]::TryParse($id, [ref]$n)
if ($id -eq '' -or $n -lt 1) {
    Write-Output 'BADID'
    exit 1
}
if ($h -eq '') {
    Write-Output 'BADHASH'
    exit 1
}
if ($password.Length -lt 12 -or $password.Length -gt 256 -or $password -eq 'change-me-to-a-long-strong-password') {
    Write-Output 'BADPASSWORD'
    exit 1
}
if ($secret.Length -lt 48 -or $secret.Length -gt 1024) {
    Write-Output 'BADSECRET'
    exit 1
}
Write-Output 'OK'
exit 0
