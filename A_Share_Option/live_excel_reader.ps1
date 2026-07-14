param(
    [Parameter(Mandatory=$true)][string]$WorkbookPath,
    [Parameter(Mandatory=$true)][string]$SheetName
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$targetPath = ""
try {
    $targetPath = (Resolve-Path -LiteralPath $WorkbookPath).Path
} catch {
    $targetPath = $WorkbookPath
}

$excel = [Runtime.InteropServices.Marshal]::GetActiveObject("Excel.Application")
$workbook = $null
foreach ($book in @($excel.Workbooks)) {
    $fullName = ""
    try { $fullName = [string]$book.FullName } catch { $fullName = "" }
    if ($fullName -and ($fullName -ieq $targetPath)) {
        $workbook = $book
        break
    }
    if (($book.Name) -and ($targetPath.EndsWith($book.Name, [StringComparison]::OrdinalIgnoreCase))) {
        $workbook = $book
        break
    }
}

if ($null -eq $workbook) {
    throw "Workbook is not open in Excel: $WorkbookPath"
}

$sheetNames = @()
foreach ($sheet in @($workbook.Worksheets)) {
    $sheetNames += [string]$sheet.Name
}

$worksheet = $null
foreach ($sheet in @($workbook.Worksheets)) {
    if ([string]$sheet.Name -eq $SheetName) {
        $worksheet = $sheet
        break
    }
}
if ($null -eq $worksheet) {
    $worksheet = $workbook.Worksheets.Item(1)
}

$used = $worksheet.UsedRange
$values = $used.Value2
$rowCount = [int]$used.Rows.Count
$colCount = [int]$used.Columns.Count
$rows = New-Object System.Collections.Generic.List[object]

if ($rowCount -eq 1 -and $colCount -eq 1) {
    $rows.Add(@($values))
} else {
    for ($r = 1; $r -le $rowCount; $r++) {
        $row = New-Object System.Collections.Generic.List[object]
        for ($c = 1; $c -le $colCount; $c++) {
            $row.Add($values.GetValue($r, $c))
        }
        $rows.Add($row.ToArray())
    }
}

[pscustomobject]@{
    ok = $true
    source = $targetPath
    sheet = [string]$worksheet.Name
    requestedSheet = $SheetName
    availableSheets = $sheetNames
    readAt = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    matrix = $rows.ToArray()
} | ConvertTo-Json -Depth 8 -Compress
