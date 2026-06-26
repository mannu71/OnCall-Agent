/**
 * Strip the Python-repr content-block artefact from older LLM output.
 * @param {string} text
 * @returns {string}
 */
export function cleanLlmText(text) {
  if (!text || typeof text !== 'string') return text || '';
  const trimmed = text.trimStart();
  if (!trimmed.startsWith('[{')) return text;

  const dq = [...text.matchAll(/'text':\s*"([\s\S]*?)"\s*[,}]/g)];
  if (dq.length) return dq.map(m => m[1]).join('').replace(/\\n/g, '\n').replace(/\\'/g, "'").replace(/\\"/g, '"');

  const sq = [...text.matchAll(/'text':\s*'([\s\S]*?)'\s*[,}]/g)];
  if (sq.length) return sq.map(m => m[1]).join('').replace(/\\n/g, '\n');

  try {
    const asJson = text
      .replace(/'/g, '"')
      .replace(/\bNone\b/g, 'null')
      .replace(/\bTrue\b/g, 'true')
      .replace(/\bFalse\b/g, 'false');
    const blocks = JSON.parse(asJson);
    if (Array.isArray(blocks)) {
      const parts = blocks.filter(b => b.type === 'text').map(b => b.text || '');
      if (parts.length) return parts.join('');
    }
  } catch { /* ignore */ }

  return text;
}
