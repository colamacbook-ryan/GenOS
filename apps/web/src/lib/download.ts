/**
 * Save text as a file (CSV export). The API sends a UTF-8 BOM so Excel reads
 * Vietnamese correctly; `Response.text()` strips it, so it is put back here.
 */
export function downloadText(text: string, filename: string, type = 'text/csv;charset=utf-8'): void {
  const bom = type.startsWith('text/csv') && !text.startsWith('\uFEFF') ? '\uFEFF' : '';
  const blob = new Blob([bom + text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Save a binary file (backup download). */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
