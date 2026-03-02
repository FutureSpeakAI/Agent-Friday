/**
 * office.ts -- Microsoft Office COM automation via PowerShell.
 *
 * Provides tool declarations and an executor for creating / reading / manipulating
 * Word, Excel, and PowerPoint documents on Windows through the COM interop layer.
 *
 * Prerequisites: Microsoft Office (Word, Excel, PowerPoint) installed on the host.
 * All operations run headless with alerts suppressed.
 */

import { execFileSync } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: 'object';
    properties: Record<string, any>;
    required: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// PowerShell helper
// ---------------------------------------------------------------------------

const PS_TIMEOUT = 60_000; // 60 s -- Office COM can be slow

/**
 * Execute a PowerShell script string and return its stdout.
 * Uses UTF-8 I/O and a 60-second timeout.
 */
function runPS(script: string): string {
  // Write script to a temp file to avoid quoting issues with inline -Command
  const tmp = path.join(
    process.env.TEMP || process.env.TMP || 'C:\\Windows\\Temp',
    `friday_office_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.ps1`,
  );
  try {
    fs.writeFileSync(tmp, script, 'utf-8');
    // Crypto Sprint 13: Use execFileSync to avoid shell interpolation of temp path.
    const out = execFileSync(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', tmp],
      {
        timeout: PS_TIMEOUT,
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      },
    );
    return (out ?? '').trim();
  } finally {
    try { fs.unlinkSync(tmp); } catch { /* ignore */ }
  }
}

/**
 * Normalise a user-supplied file path to an absolute Windows path.
 */
function winPath(p: string): string {
  const resolved = path.resolve(p);
  return resolved.replace(/\//g, '\\');
}

/**
 * Escape a string for safe interpolation into a PowerShell single-quoted literal.
 * Single quotes are doubled per PowerShell quoting rules.
 */
function psEscape(s: string): string {
  return s.replace(/'/g, "''");
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // ---- Word ---------------------------------------------------------------
  {
    name: 'word_create',
    description:
      'Create a new Word document (.docx). Content lines starting with # / ## / ### are converted to Heading 1/2/3; all other lines become body paragraphs.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Absolute or relative path for the new .docx file.' },
        content: { type: 'string', description: 'Document body text. Use # for headings.' },
        title: { type: 'string', description: 'Optional document title (set in built-in properties).' },
      },
      required: ['file_path', 'content'],
    },
  },
  {
    name: 'word_read',
    description: 'Read all text content from a Word document, returned as plain text with paragraphs separated by newlines.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .docx file to read.' },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'word_append',
    description: 'Append text to the end of an existing Word document.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the existing .docx file.' },
        content: { type: 'string', description: 'Text to append. Supports # headings.' },
      },
      required: ['file_path', 'content'],
    },
  },
  {
    name: 'word_find_replace',
    description: 'Find and replace text in a Word document.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .docx file.' },
        find: { type: 'string', description: 'Text to find.' },
        replace: { type: 'string', description: 'Replacement text.' },
        all: { type: 'boolean', description: 'Replace all occurrences (default true).' },
      },
      required: ['file_path', 'find', 'replace'],
    },
  },
  {
    name: 'word_to_pdf',
    description: 'Convert a Word document to PDF.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .docx file.' },
        output_path: { type: 'string', description: 'Output PDF path. Defaults to same name with .pdf extension.' },
      },
      required: ['file_path'],
    },
  },

  // ---- Excel --------------------------------------------------------------
  {
    name: 'excel_create',
    description: 'Create a new Excel workbook (.xlsx) with optional sheets and data.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path for the new .xlsx file.' },
        sheets: {
          type: 'array',
          description: 'Array of sheets. Each has a name and a 2D string array of data.',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              data: {
                type: 'array',
                items: { type: 'array', items: { type: 'string' } },
              },
            },
            required: ['name', 'data'],
          },
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'excel_read',
    description:
      'Read data from an Excel workbook. Returns a text-formatted table of the used range or a specified range.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .xlsx file.' },
        sheet: { type: 'string', description: 'Sheet name. Defaults to the first sheet.' },
        range: { type: 'string', description: 'Cell range, e.g. "A1:D10". Defaults to the used range.' },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'excel_write_cell',
    description: 'Write a value to a specific cell in an Excel workbook.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .xlsx file.' },
        sheet: { type: 'string', description: 'Sheet name. Defaults to the first sheet.' },
        cell: { type: 'string', description: 'Cell reference, e.g. "A1" or "B5".' },
        value: { type: 'string', description: 'Value to write.' },
      },
      required: ['file_path', 'cell', 'value'],
    },
  },
  {
    name: 'excel_add_formula',
    description: 'Set a formula on a cell in an Excel workbook.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .xlsx file.' },
        sheet: { type: 'string', description: 'Sheet name. Defaults to the first sheet.' },
        cell: { type: 'string', description: 'Cell reference, e.g. "C1".' },
        formula: { type: 'string', description: 'Formula string, e.g. "=SUM(A1:A10)".' },
      },
      required: ['file_path', 'cell', 'formula'],
    },
  },
  {
    name: 'excel_run_macro',
    description:
      'Run a VBA macro inside an Excel workbook. WARNING: macros execute arbitrary code -- use with caution.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .xlsm/.xlsx file containing the macro.' },
        macro_name: { type: 'string', description: 'Fully qualified macro name, e.g. "Module1.MyMacro".' },
        args: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional arguments to pass to the macro.',
        },
      },
      required: ['file_path', 'macro_name'],
    },
  },
  {
    name: 'excel_to_csv',
    description: 'Export an Excel sheet to a CSV file.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .xlsx file.' },
        sheet: { type: 'string', description: 'Sheet name. Defaults to the first sheet.' },
        output_path: { type: 'string', description: 'Path for the output .csv file.' },
      },
      required: ['file_path', 'output_path'],
    },
  },

  // ---- PowerPoint ---------------------------------------------------------
  {
    name: 'powerpoint_create',
    description:
      'Create a new PowerPoint presentation (.pptx) with the specified slides.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path for the new .pptx file.' },
        slides: {
          type: 'array',
          description: 'Slides to add.',
          items: {
            type: 'object',
            properties: {
              title: { type: 'string', description: 'Slide title.' },
              content: { type: 'string', description: 'Body text.' },
              layout: {
                type: 'string',
                enum: ['title', 'content', 'blank'],
                description: 'Slide layout: title (Title Slide), content (Title and Content), blank.',
              },
            },
            required: ['title'],
          },
        },
      },
      required: ['file_path', 'slides'],
    },
  },
  {
    name: 'powerpoint_read',
    description: 'Read text content from all slides of a PowerPoint presentation.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .pptx file.' },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'powerpoint_add_slide',
    description: 'Add a slide to an existing PowerPoint presentation.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .pptx file.' },
        title: { type: 'string', description: 'Slide title.' },
        content: { type: 'string', description: 'Body text for the slide.' },
        position: { type: 'number', description: '1-based position to insert. Defaults to end.' },
      },
      required: ['file_path', 'title'],
    },
  },
  {
    name: 'powerpoint_to_pdf',
    description: 'Convert a PowerPoint presentation to PDF.',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: 'Path to the .pptx file.' },
        output_path: { type: 'string', description: 'Output PDF path. Defaults to same name with .pdf.' },
      },
      required: ['file_path'],
    },
  },
];

// ---------------------------------------------------------------------------
// PowerShell script builders
// ---------------------------------------------------------------------------

// ---- Word helpers ---------------------------------------------------------

/**
 * Build PowerShell lines that add paragraphs to an open Word document.
 * Lines starting with #, ##, ### become Heading 1/2/3; the rest are Normal.
 */
function wordParagraphLines(docVar: string, content: string): string {
  const lines = content.split('\n');
  const ps: string[] = [];
  for (const raw of lines) {
    const line = raw.replace(/\r$/, '');
    let style = 'wdStyleNormal';
    let text = line;
    if (line.startsWith('### ')) {
      style = 'wdStyleHeading3';
      text = line.slice(4);
    } else if (line.startsWith('## ')) {
      style = 'wdStyleHeading2';
      text = line.slice(3);
    } else if (line.startsWith('# ')) {
      style = 'wdStyleHeading1';
      text = line.slice(2);
    }
    // -16 = wdStyleNormal, -2/-3/-4 = Heading 1/2/3
    const styleEnum =
      style === 'wdStyleHeading1' ? -2 :
      style === 'wdStyleHeading2' ? -3 :
      style === 'wdStyleHeading3' ? -4 :
      -1; // wdStyleNormal
    ps.push(
      `$p = ${docVar}.Content.Paragraphs.Add()`,
      `$p.Range.Text = '${psEscape(text)}'`,
      `$p.Style = ${docVar}.Styles.Item(${styleEnum})`,
      `$p.Range.InsertParagraphAfter()`,
    );
  }
  return ps.join('\n');
}

function scriptWordCreate(filePath: string, content: string, title?: string): string {
  const fp = psEscape(winPath(filePath));
  const titleLine = title
    ? `$doc.BuiltInDocumentProperties.Item(1).Value = '${psEscape(title)}'`
    : '';
  return `
$app = New-Object -ComObject Word.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = 0  # wdAlertsNone
  $doc = $app.Documents.Add()
  ${titleLine}
${wordParagraphLines('$doc', content)}
  $doc.SaveAs2([ref]'${fp}', [ref]16)  # 16 = wdFormatDocumentDefault (.docx)
  $doc.Close([ref]0)
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptWordRead(filePath: string): string {
  const fp = psEscape(winPath(filePath));
  return `
$app = New-Object -ComObject Word.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = 0
  $doc = $app.Documents.Open('${fp}', $false, $true)  # ReadOnly
  $sb = [System.Text.StringBuilder]::new()
  foreach ($p in $doc.Paragraphs) {
    [void]$sb.AppendLine($p.Range.Text.TrimEnd([char]13, [char]7))
  }
  $doc.Close([ref]0)
  Write-Output $sb.ToString()
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptWordAppend(filePath: string, content: string): string {
  const fp = psEscape(winPath(filePath));
  return `
$app = New-Object -ComObject Word.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = 0
  $doc = $app.Documents.Open('${fp}')
  # Move to the end of the document
  $range = $doc.Content
  $range.Collapse(0)  # wdCollapseEnd
${wordParagraphLines('$doc', content)}
  $doc.Save()
  $doc.Close([ref]0)
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptWordFindReplace(filePath: string, find: string, replace: string, all: boolean): string {
  const fp = psEscape(winPath(filePath));
  const replaceConst = all ? 2 : 1; // wdReplaceAll = 2, wdReplaceOne = 1
  return `
$app = New-Object -ComObject Word.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = 0
  $doc = $app.Documents.Open('${fp}')
  $findObj = $doc.Content.Find
  $findObj.ClearFormatting()
  $findObj.Replacement.ClearFormatting()
  $findObj.Text = '${psEscape(find)}'
  $findObj.Replacement.Text = '${psEscape(replace)}'
  $findObj.Forward = $true
  $findObj.Wrap = 1  # wdFindContinue
  $findObj.Format = $false
  $findObj.MatchCase = $false
  $findObj.MatchWholeWord = $false
  $result = $findObj.Execute(
    '${psEscape(find)}',   # FindText
    $false,                # MatchCase
    $false,                # MatchWholeWord
    $false,                # MatchWildcards
    $false,                # MatchSoundsLike
    $false,                # MatchAllWordForms
    $true,                 # Forward
    1,                     # Wrap (wdFindContinue)
    $false,                # Format
    '${psEscape(replace)}', # ReplaceWith
    ${replaceConst}        # Replace
  )
  $doc.Save()
  $doc.Close([ref]0)
  if ($result) { Write-Output 'Replaced successfully.' } else { Write-Output 'No matches found.' }
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptWordToPdf(filePath: string, outputPath: string): string {
  const fp = psEscape(winPath(filePath));
  const op = psEscape(winPath(outputPath));
  return `
$app = New-Object -ComObject Word.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = 0
  $doc = $app.Documents.Open('${fp}', $false, $true)
  $doc.ExportAsFixedFormat('${op}', 17)  # 17 = wdExportFormatPDF
  $doc.Close([ref]0)
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

// ---- Excel helpers --------------------------------------------------------

function scriptExcelCreate(
  filePath: string,
  sheets?: Array<{ name: string; data: string[][] }>,
): string {
  const fp = psEscape(winPath(filePath));

  let sheetScript = '';
  if (sheets && sheets.length > 0) {
    const parts: string[] = [];
    for (let si = 0; si < sheets.length; si++) {
      const s = sheets[si];
      const wsVar = `$ws${si}`;
      if (si === 0) {
        // Rename the default first sheet
        parts.push(`${wsVar} = $wb.Sheets.Item(1)`);
        parts.push(`${wsVar}.Name = '${psEscape(s.name)}'`);
      } else {
        parts.push(
          `${wsVar} = $wb.Sheets.Add([System.Reflection.Missing]::Value, $wb.Sheets.Item($wb.Sheets.Count))`,
        );
        parts.push(`${wsVar}.Name = '${psEscape(s.name)}'`);
      }
      for (let r = 0; r < s.data.length; r++) {
        for (let c = 0; c < s.data[r].length; c++) {
          const cellRef = `${wsVar}.Cells.Item(${r + 1}, ${c + 1})`;
          parts.push(`${cellRef}.Value2 = '${psEscape(s.data[r][c])}'`);
        }
      }
    }
    sheetScript = parts.join('\n  ');
  }

  return `
$app = New-Object -ComObject Excel.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = $false
  $wb = $app.Workbooks.Add()
  ${sheetScript}
  $wb.SaveAs('${fp}', 51)  # 51 = xlOpenXMLWorkbook (.xlsx)
  $wb.Close($false)
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptExcelRead(filePath: string, sheet?: string, range?: string): string {
  const fp = psEscape(winPath(filePath));
  const sheetSelector = sheet
    ? `$ws = $wb.Sheets.Item('${psEscape(sheet)}')`
    : '$ws = $wb.Sheets.Item(1)';
  const rangeSelector = range
    ? `$rng = $ws.Range('${psEscape(range)}')`
    : '$rng = $ws.UsedRange';

  return `
$app = New-Object -ComObject Excel.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = $false
  $wb = $app.Workbooks.Open('${fp}', 0, $true)  # ReadOnly
  ${sheetSelector}
  ${rangeSelector}
  $rows = $rng.Rows.Count
  $cols = $rng.Columns.Count
  $sb = [System.Text.StringBuilder]::new()
  for ($r = 1; $r -le $rows; $r++) {
    $line = @()
    for ($c = 1; $c -le $cols; $c++) {
      $val = $rng.Cells.Item($r, $c).Text
      if ($null -eq $val) { $val = '' }
      $line += $val
    }
    [void]$sb.AppendLine(($line -join "\`t"))
  }
  $wb.Close($false)
  Write-Output $sb.ToString()
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptExcelWriteCell(filePath: string, cell: string, value: string, sheet?: string): string {
  const fp = psEscape(winPath(filePath));
  const sheetSel = sheet
    ? `$ws = $wb.Sheets.Item('${psEscape(sheet)}')`
    : '$ws = $wb.Sheets.Item(1)';
  return `
$app = New-Object -ComObject Excel.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = $false
  $wb = $app.Workbooks.Open('${fp}')
  ${sheetSel}
  $ws.Range('${psEscape(cell)}').Value2 = '${psEscape(value)}'
  $wb.Save()
  $wb.Close($false)
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptExcelAddFormula(filePath: string, cell: string, formula: string, sheet?: string): string {
  const fp = psEscape(winPath(filePath));
  const sheetSel = sheet
    ? `$ws = $wb.Sheets.Item('${psEscape(sheet)}')`
    : '$ws = $wb.Sheets.Item(1)';
  return `
$app = New-Object -ComObject Excel.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = $false
  $wb = $app.Workbooks.Open('${fp}')
  ${sheetSel}
  $ws.Range('${psEscape(cell)}').Formula = '${psEscape(formula)}'
  $wb.Save()
  $wb.Close($false)
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptExcelRunMacro(filePath: string, macroName: string, args?: string[]): string {
  const fp = psEscape(winPath(filePath));
  let runLine: string;
  if (args && args.length > 0) {
    const argList = args.map((a) => `'${psEscape(a)}'`).join(', ');
    runLine = `$app.Run('${psEscape(macroName)}', ${argList})`;
  } else {
    runLine = `$app.Run('${psEscape(macroName)}')`;
  }
  return `
$app = New-Object -ComObject Excel.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = $false
  $app.AutomationSecurity = 1  # msoAutomationSecurityLow -- required for macros
  $wb = $app.Workbooks.Open('${fp}')
  ${runLine}
  $wb.Save()
  $wb.Close($false)
  Write-Output 'Macro executed.'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptExcelToCsv(filePath: string, outputPath: string, sheet?: string): string {
  const fp = psEscape(winPath(filePath));
  const op = psEscape(winPath(outputPath));
  const sheetSel = sheet
    ? `$ws = $wb.Sheets.Item('${psEscape(sheet)}')`
    : '$ws = $wb.Sheets.Item(1)';
  return `
$app = New-Object -ComObject Excel.Application
try {
  $app.Visible = $false
  $app.DisplayAlerts = $false
  $wb = $app.Workbooks.Open('${fp}', 0, $true)
  ${sheetSel}
  $ws.Copy()                          # Copy sheet to a new temp workbook
  $tempWb = $app.ActiveWorkbook
  $tempWb.SaveAs('${op}', 6)          # 6 = xlCSV
  $tempWb.Close($false)
  $wb.Close($false)
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

// ---- PowerPoint helpers ---------------------------------------------------

function scriptPptCreate(
  filePath: string,
  slides: Array<{ title: string; content?: string; layout?: 'title' | 'content' | 'blank' }>,
): string {
  const fp = psEscape(winPath(filePath));

  // ppLayoutTitle = 1, ppLayoutText = 2 (Title+Content), ppLayoutBlank = 12
  const layoutMap: Record<string, number> = { title: 1, content: 2, blank: 12 };

  const slideLines: string[] = [];
  for (let i = 0; i < slides.length; i++) {
    const s = slides[i];
    const layoutId = layoutMap[s.layout ?? 'content'] ?? 2;
    slideLines.push(`$layout = $pres.SlideLayouts | Where-Object { $_.Layout -eq ${layoutId} } | Select-Object -First 1`);
    slideLines.push(`if (-not $layout) { $layout = $pres.SlideLayouts | Select-Object -First 1 }`);
    slideLines.push(`$slide = $pres.Slides.AddSlide(${i + 1}, $layout)`);
    // Title placeholder is usually index 1
    if (s.layout !== 'blank') {
      slideLines.push(`try { $slide.Shapes.Item(1).TextFrame.TextRange.Text = '${psEscape(s.title)}' } catch {}`);
    }
    // Content placeholder is usually index 2
    if (s.content && s.layout !== 'blank') {
      slideLines.push(`try { $slide.Shapes.Item(2).TextFrame.TextRange.Text = '${psEscape(s.content)}' } catch {}`);
    }
  }

  return `
$app = New-Object -ComObject PowerPoint.Application
try {
  # PowerPoint COM requires Visible for some operations but we minimise
  $pres = $app.Presentations.Add($true)  # WithWindow -- required for layout access
  $app.WindowState = 2  # ppWindowMinimized
${slideLines.map((l) => '  ' + l).join('\n')}
  $pres.SaveAs('${fp}')
  $pres.Close()
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptPptRead(filePath: string): string {
  const fp = psEscape(winPath(filePath));
  return `
$app = New-Object -ComObject PowerPoint.Application
try {
  $pres = $app.Presentations.Open('${fp}', $true, $false, $false)  # ReadOnly, NoWindow
  $sb = [System.Text.StringBuilder]::new()
  foreach ($slide in $pres.Slides) {
    [void]$sb.AppendLine("--- Slide $($slide.SlideIndex) ---")
    foreach ($shape in $slide.Shapes) {
      if ($shape.HasTextFrame -eq -1) {
        $text = $shape.TextFrame.TextRange.Text
        if ($text.Trim().Length -gt 0) {
          [void]$sb.AppendLine($text)
        }
      }
    }
    [void]$sb.AppendLine('')
  }
  $pres.Close()
  Write-Output $sb.ToString()
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptPptAddSlide(filePath: string, title: string, content?: string, position?: number): string {
  const fp = psEscape(winPath(filePath));
  const posExpr = position != null ? String(position) : '$pres.Slides.Count + 1';
  const contentLine = content
    ? `try { $slide.Shapes.Item(2).TextFrame.TextRange.Text = '${psEscape(content)}' } catch {}`
    : '';
  return `
$app = New-Object -ComObject PowerPoint.Application
try {
  $pres = $app.Presentations.Open('${fp}', $false, $false, $true)
  $app.WindowState = 2
  $pos = ${posExpr}
  $layout = $pres.SlideLayouts | Where-Object { $_.Layout -eq 2 } | Select-Object -First 1
  if (-not $layout) { $layout = $pres.SlideLayouts | Select-Object -First 1 }
  $slide = $pres.Slides.AddSlide($pos, $layout)
  try { $slide.Shapes.Item(1).TextFrame.TextRange.Text = '${psEscape(title)}' } catch {}
  ${contentLine}
  $pres.Save()
  $pres.Close()
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

function scriptPptToPdf(filePath: string, outputPath: string): string {
  const fp = psEscape(winPath(filePath));
  const op = psEscape(winPath(outputPath));
  return `
$app = New-Object -ComObject PowerPoint.Application
try {
  $pres = $app.Presentations.Open('${fp}', $true, $false, $false)
  $pres.ExportAsFixedFormat(2, '${op}')  # 2 = ppFixedFormatTypePDF
  $pres.Close()
  Write-Output 'OK'
} finally {
  $app.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

// ---------------------------------------------------------------------------
// detect()
// ---------------------------------------------------------------------------

/**
 * Check whether Microsoft Office is installed.
 * Tries the registry first, then falls back to instantiating the Word COM class.
 */
export async function detect(): Promise<boolean> {
  try {
    const script = `
$found = $false
try {
  $roots = Get-ChildItem 'HKLM:\\SOFTWARE\\Microsoft\\Office' -ErrorAction SilentlyContinue |
    Where-Object { $_.PSChildName -match '^\\d+\\.\\d+$' }
  foreach ($ver in $roots) {
    $common = Join-Path $ver.PSPath 'Common\\InstallRoot'
    if (Test-Path $common) { $found = $true; break }
  }
} catch {}
if (-not $found) {
  try {
    $w = New-Object -ComObject Word.Application
    $w.Quit()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($w) | Out-Null
    $found = $true
  } catch {}
}
Write-Output $found
`;
    const out = runPS(script);
    return out.trim().toLowerCase() === 'true';
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// execute()
// ---------------------------------------------------------------------------

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      // -- Word -------------------------------------------------------------
      case 'word_create': {
        const filePath = String(args.file_path);
        const content = String(args.content);
        const title = args.title != null ? String(args.title) : undefined;
        runPS(scriptWordCreate(filePath, content, title));
        return { result: `Word document created: ${winPath(filePath)}` };
      }

      case 'word_read': {
        const filePath = String(args.file_path);
        const text = runPS(scriptWordRead(filePath));
        return { result: text };
      }

      case 'word_append': {
        const filePath = String(args.file_path);
        const content = String(args.content);
        runPS(scriptWordAppend(filePath, content));
        return { result: `Content appended to ${winPath(filePath)}` };
      }

      case 'word_find_replace': {
        const filePath = String(args.file_path);
        const find = String(args.find);
        const replace = String(args.replace);
        const all = args.all !== false; // default true
        const msg = runPS(scriptWordFindReplace(filePath, find, replace, all));
        return { result: msg };
      }

      case 'word_to_pdf': {
        const filePath = String(args.file_path);
        const outputPath = args.output_path
          ? String(args.output_path)
          : filePath.replace(/\.[^.]+$/, '.pdf');
        runPS(scriptWordToPdf(filePath, outputPath));
        return { result: `PDF saved: ${winPath(outputPath)}` };
      }

      // -- Excel ------------------------------------------------------------
      case 'excel_create': {
        const filePath = String(args.file_path);
        const sheets = args.sheets as
          | Array<{ name: string; data: string[][] }>
          | undefined;
        runPS(scriptExcelCreate(filePath, sheets));
        return { result: `Excel workbook created: ${winPath(filePath)}` };
      }

      case 'excel_read': {
        const filePath = String(args.file_path);
        const sheet = args.sheet != null ? String(args.sheet) : undefined;
        const range = args.range != null ? String(args.range) : undefined;
        const text = runPS(scriptExcelRead(filePath, sheet, range));
        return { result: text };
      }

      case 'excel_write_cell': {
        const filePath = String(args.file_path);
        const cell = String(args.cell);
        const value = String(args.value);
        const sheet = args.sheet != null ? String(args.sheet) : undefined;
        runPS(scriptExcelWriteCell(filePath, cell, value, sheet));
        return { result: `Cell ${cell} updated in ${winPath(filePath)}` };
      }

      case 'excel_add_formula': {
        const filePath = String(args.file_path);
        const cell = String(args.cell);
        const formula = String(args.formula);
        const sheet = args.sheet != null ? String(args.sheet) : undefined;
        runPS(scriptExcelAddFormula(filePath, cell, formula, sheet));
        return { result: `Formula set on ${cell} in ${winPath(filePath)}` };
      }

      case 'excel_run_macro': {
        const filePath = String(args.file_path);
        const macroName = String(args.macro_name);
        const macroArgs = args.args as string[] | undefined;
        const msg = runPS(scriptExcelRunMacro(filePath, macroName, macroArgs));
        return { result: msg };
      }

      case 'excel_to_csv': {
        const filePath = String(args.file_path);
        const outputPath = String(args.output_path);
        const sheet = args.sheet != null ? String(args.sheet) : undefined;
        runPS(scriptExcelToCsv(filePath, outputPath, sheet));
        return { result: `CSV exported: ${winPath(outputPath)}` };
      }

      // -- PowerPoint -------------------------------------------------------
      case 'powerpoint_create': {
        const filePath = String(args.file_path);
        const slides = args.slides as Array<{
          title: string;
          content?: string;
          layout?: 'title' | 'content' | 'blank';
        }>;
        runPS(scriptPptCreate(filePath, slides));
        return { result: `Presentation created: ${winPath(filePath)}` };
      }

      case 'powerpoint_read': {
        const filePath = String(args.file_path);
        const text = runPS(scriptPptRead(filePath));
        return { result: text };
      }

      case 'powerpoint_add_slide': {
        const filePath = String(args.file_path);
        const title = String(args.title);
        const content = args.content != null ? String(args.content) : undefined;
        const position = args.position != null ? Number(args.position) : undefined;
        runPS(scriptPptAddSlide(filePath, title, content, position));
        return { result: `Slide added to ${winPath(filePath)}` };
      }

      case 'powerpoint_to_pdf': {
        const filePath = String(args.file_path);
        const outputPath = args.output_path
          ? String(args.output_path)
          : filePath.replace(/\.[^.]+$/, '.pdf');
        runPS(scriptPptToPdf(filePath, outputPath));
        return { result: `PDF saved: ${winPath(outputPath)}` };
      }

      default:
        return { error: `Unknown tool: ${toolName}` };
    }
  } catch (err: unknown) {
    const message =
      err instanceof Error ? err.message : String(err);
    return { error: `Office tool "${toolName}" failed: ${message}` };
  }
}
