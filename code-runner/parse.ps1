param([string]$Path)
$tokens = $null
$problems = $null
[System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$problems) | Out-Null
if ($problems.Count -gt 0) {
    foreach ($problem in $problems) {
        "script.ps1:$($problem.Extent.StartLineNumber):$($problem.Extent.StartColumnNumber): ParserError: $($problem.Message)"
    }
    exit 1
}
