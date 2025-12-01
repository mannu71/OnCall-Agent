import { parse as parseSQL } from 'pgsql-ast-parser';
import logger from '../shared/logger.js';

/**
 * sql-ast-parser.js
 *
 * - Robust splitter with quote/dollar/comment awareness
 * - AST fallback when available
 * - Variable normalization {var} -> {{var}}
 * - Dependency graph + parallel-group detection preserved
 * - Metadata extraction (YAML-like) from leading comment block
 *   - Supports: "-- key: value" lines and leading /* block comments *\/
 *   - label: must be unique (throws if duplicate)
 */

 /* -------------------------
  * DB section detection
  * ------------------------- */
function detectDbSections(sqlText) {
  const lines = sqlText.split(/\r?\n/);
  const sections = [];
  let offset = 0;
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    const m = trimmed.match(/^--\s*db(?:\s*[:=]\s*|\s+)(.+)$/i);
    if (m) {
      const dbRaw = m[1].trim();
      // Split on whitespace or -- to remove trailing comments, then take first part
      const dbPart = dbRaw.split(/\s+--/)[0].trim();
      // Support comma-separated databases: postgres-production, postgres-dev
      const dbs = dbPart.split(',').map(d => d.trim()).filter(Boolean);
      sections.push({ lineIndex: i, charOffset: offset, dbs });
    }
    offset += lines[i].length + 1;
  }
  return sections;
}

function resolveDbsForPosition(pos, dbSections, orchestratorData) {
  if (!dbSections || dbSections.length === 0) return [];
  let chosen = dbSections[0].dbs;
  for (const s of dbSections) {
    if (s.charOffset <= pos) chosen = s.dbs;
    else break;
  }
  const tools = orchestratorData?.tools || [];
  logger.info(`resolveDbsForPosition: pos=${pos}, chosen=${JSON.stringify(chosen)}, tools=${tools.map(t => t.data?.label || t.id).join(',')}`);
  const serverIds = [];
  for (const dbName of chosen) {
    // Match against tool.data.label (e.g., "postgres-production") which is the MCP server name
    const match = tools.find(t => t.data?.label?.toLowerCase() === dbName.toLowerCase());
    if (match) {
      serverIds.push(match.id);
      logger.info(`Matched dbName="${dbName}" to tool ID="${match.id}" (label="${match.data.label}")`);
    } else {
      logger.warn(`No match found for dbName="${dbName}" in available tools: ${tools.map(t => t.data?.label).join(', ')}`);
    }
  }
  return serverIds;
}

/* -------------------------
 * Robust SQL splitter
 * ------------------------- */
function splitSqlStatements(sqlText) {
  const res = [];
  let i = 0;
  const len = sqlText.length;
  let bufStart = 0;
  const state = { inS: false, inD: false, inDollar: null, paren: 0, prev: null };

  const isIdChar = ch => /[A-Za-z0-9_]/.test(ch);

  while (i < len) {
    const ch = sqlText[i];

    // Dollar-quoted strings
    if (!state.inS && !state.inD && ch === '$') {
      let j = i + 1;
      while (j < len && isIdChar(sqlText[j])) j++;
      if (sqlText[j] === '$') {
        const tag = sqlText.slice(i, j + 1);
        if (!state.inDollar) {
          state.inDollar = tag;
          i = j + 1; state.prev = null; continue;
        } else if (state.inDollar === tag) {
          state.inDollar = null;
          i = j + 1; state.prev = null; continue;
        }
      }
    }

    if (!state.inDollar) {
      if (ch === "'" && state.prev !== '\\' && !state.inD) state.inS = !state.inS;
      else if (ch === '"' && state.prev !== '\\' && !state.inS) state.inD = !state.inD;
    }

    if (!state.inS && !state.inD && !state.inDollar) {
      if (ch === '(') state.paren++;
      else if (ch === ')') state.paren = Math.max(0, state.paren - 1);
    }

    if (ch === ';' && !state.inS && !state.inD && !state.inDollar && state.paren === 0) {
      const chunk = sqlText.slice(bufStart, i + 1).trim();
      if (chunk) res.push({ sql: chunk, start: bufStart, end: i + 1 });
      bufStart = i + 1;
    }

    state.prev = ch;
    i++;
  }

  const tail = sqlText.slice(bufStart).trim();
  if (tail) res.push({ sql: tail, start: bufStart, end: len });

  return res;
}

/* -------------------------------------------------
 * normalizeVars: convert {var} -> {{var}} outside quotes/comments/dollar
 * ------------------------------------------------- */
function normalizeVars(sql) {
  let out = '';
  const len = sql.length;
  let i = 0;

  let inSingle = false;
  let inDouble = false;
  let inDollar = null;
  let inLineComment = false;
  let inBlockComment = false;

  const isIdStart = ch => /[A-Za-z_]/.test(ch);
  const isIdChar = ch => /[A-Za-z0-9_\.]/.test(ch); // allow dots for step.column

  while (i < len) {
    const ch = sql[i];

    // line comment start
    if (!inBlockComment && !inSingle && !inDouble && !inDollar) {
      if (ch === '-' && sql[i + 1] === '-') {
        inLineComment = true;
        out += '--';
        i += 2;
        continue;
      }
    }
    if (inLineComment) {
      out += ch;
      if (ch === '\n') inLineComment = false;
      i++;
      continue;
    }

    // block comment
    if (!inSingle && !inDouble && !inLineComment && !inDollar) {
      if (ch === '/' && sql[i + 1] === '*') {
        inBlockComment = true;
        out += '/*';
        i += 2;
        continue;
      }
    }
    if (inBlockComment) {
      out += ch;
      if (ch === '*' && sql[i + 1] === '/') {
        out += '/';
        inBlockComment = false;
        i += 2;
      } else i++;
      continue;
    }

    // dollar-quoted
    if (!inSingle && !inDouble) {
      if (!inDollar && ch === '$') {
        let j = i + 1;
        while (j < len && isIdChar(sql[j])) j++;
        if (sql[j] === '$') {
          inDollar = sql.slice(i, j + 1);
          out += inDollar;
          i = j + 1;
          continue;
        }
      }
    }
    if (inDollar) {
      if (sql.startsWith(inDollar, i)) {
        out += inDollar;
        i += inDollar.length;
        inDollar = null;
      } else {
        out += ch;
        i++;
      }
      continue;
    }

    // quotes
    if (ch === "'" && !inDouble) { inSingle = !inSingle; out += ch; i++; continue; }
    if (ch === '"' && !inSingle) { inDouble = !inDouble; out += ch; i++; continue; }

    if (inSingle || inDouble) { out += ch; i++; continue; }

    // {identifier} or {step.col} outside of quotes/comments/dollar
    if (ch === '{' && isIdStart(sql[i + 1])) {
      // handle already double-brace or single-brace
      if (sql[i + 1] === '{') {
        // consume opening double braces and normalize whitespace inside
        let j = i + 2;
        // find matching '}}'
        while (j < len && !(sql[j] === '}' && sql[j + 1] === '}')) j++;
        if (j < len) {
          const inner = sql.slice(i + 2, j).replace(/\s+/g, '');
          out += `{{${inner}}}`;
          i = j + 2;
          continue;
        } else {
          out += '{{';
          i += 2;
          continue;
        }
      } else {
        // single brace — find closing single brace
        let j = i + 1;
        while (j < len && sql[j] !== '}') j++;
        if (j < len) {
          const inner = sql.slice(i + 1, j).replace(/\s+/g, '');
          // only normalize if it looks like an identifier or step.identifier (letters, digits, underscores, dots)
          if (/^[A-Za-z_][A-Za-z0-9_\.]*$/.test(inner)) {
            out += `{{${inner}}}`;
            i = j + 1;
            continue;
          } else {
            // leave as-is
            out += sql.slice(i, j + 1);
            i = j + 1;
            continue;
          }
        }
      }
    }

    out += ch;
    i++;
  }

  return out;
}

/* -------------------------
 * AST helpers
 * ------------------------- */
function extractTablesFromStmt(node) {
  const tables = new Set();
  function walk(n) {
    if (!n || typeof n !== 'object') return;
    if (n.type === 'ref' && n.table) tables.add(n.table.name.toLowerCase());
    if (n.type === 'select' && Array.isArray(n.from)) {
      for (const f of n.from) {
        if (f.type === 'table') tables.add((f.name?.name || f.name || '').toLowerCase());
        else walk(f);
      }
    }
    for (const k of Object.keys(n)) {
      if (['from','columns','args','selects','target'].includes(k)) continue;
      walk(n[k]);
    }
  }
  walk(node);
  return Array.from(tables);
}

function extractSelectColumnsFromStmt(node) {
  const cols = [];
  if (!node || node.type !== 'select') return cols;
  for (const c of node.columns || []) {
    if (c.name) cols.push(c.name);
    else if (c.expr && c.expr.type === 'ref') cols.push(c.expr.name);
    else if (c.expr && c.expr.type === 'star') cols.push('*');
    else if (c.expr && c.expr.type === 'call') cols.push(c.name || c.expr.function?.name || null);
  }
  return cols.filter(Boolean);
}

/* -------------------------
 * SQLASTParser
 * ------------------------- */
export class SQLASTParser {
  constructor() {
    this.queries = [];
    this.dependencies = new Map();
  }

  parse(sqlContent, orchestratorData = {}) {
    const dbSections = detectDbSections(sqlContent);
    let statements = null;

    // try AST parse (positions preferred)
    try {
      const ast = parseSQL(sqlContent);
      const built = [];
      let ok = true;
      for (const node of ast) {
        const start = node.range?.[0] ?? node.start ?? node.loc?.start?.offset ?? null;
        const end = node.range?.[1] ?? node.end ?? node.loc?.end?.offset ?? null;
        if (typeof start === 'number' && typeof end === 'number') {
          built.push({ sql: sqlContent.slice(start, end), start, end, astNode: node });
        } else { ok = false; break; }
      }
      if (ok) {
        // built items currently include leading comments too (we'll preserve raw)
        statements = built.map(b => ({ sql: b.sql, start: b.start, end: b.end, astNode: b.astNode }));
      } else {
        logger.debug('AST lacked positions; falling back to splitter.');
      }
    } catch (e) {
      logger.debug('AST parse failed; falling back to splitter: ' + (e && e.message));
      statements = null;
    }

    if (!statements) {
      const splits = splitSqlStatements(sqlContent);
      statements = splits.map(s => ({ sql: s.sql, start: s.start, end: s.end, astNode: null }));
      logger.debug(`Splitter produced ${statements.length} statements.`);
    }

    // build metadata & queries
    this.queries = statements.map((st, idx) => {
      // st.sql may include leading comments. Preserve raw for metadata extraction.
      const raw = st.sql;
      // Strip leading comments for normalized sql used later (keeps inline comments after first SQL line)
      const sql = raw.replace(/^(?:\s*--.*\r?\n|\s*\/\*[\s\S]*?\*\/\s*)*/, '').trim();
      const normalizedSql = normalizeVars(sql);

      /* -------------------------
       * NEW: parse leading metadata block (YAML-like)
       * - Accepts lines: -- key: value
       * - Accepts a leading /* ... *\/ block with key: value lines
       * - Ignores db: router comments (-- db:...)
       * - 'label' must be unique within the metadata block (throws if more than one)
       * ------------------------- */
      const metadata = {};
      let labelCount = 0;

      // extract leading block: scan lines from the start until the first non-comment non-empty line
      const rawLines = raw.split(/\r?\n/);
      let inBlock = false;
      const leadingMetaLines = [];

      for (const line of rawLines) {
        const t = line.trim();
        if (!t) {
          // skip empty lines but continue scanning
          continue;
        }
        // block comment start
        if (t.startsWith('/*')) {
          inBlock = true;
          // if block opens and closes on same line
          if (t.includes('*/')) {
            const inner = t.replace(/^\/\*/, '').replace(/\*\/$/, '').trim();
            if (inner) leadingMetaLines.push(inner);
            inBlock = false;
            continue;
          } else {
            const inner = t.replace(/^\/\*/, '').trim();
            if (inner) leadingMetaLines.push(inner);
            continue;
          }
        }
        if (inBlock) {
          // block comment continuation
          if (t.includes('*/')) {
            const beforeClose = t.replace(/\*\/.*$/, '').trim();
            if (beforeClose) leadingMetaLines.push(beforeClose);
            inBlock = false;
            continue;
          } else {
            leadingMetaLines.push(t);
            continue;
          }
        }

        // line comment
        if (t.startsWith('--')) {
          const after = t.replace(/^--\s*/, '');
          leadingMetaLines.push(after);
          continue;
        }

        // first non-comment line -> stop scanning leading comments
        break;
      }

      // parse key: value pairs from leadingMetaLines
      for (const ml of leadingMetaLines) {
        const m = ml.match(/^([A-Za-z0-9_\-]+)\s*:\s*(.+)$/);
        if (!m) continue;
        const key = m[1].trim();
        const val = m[2].trim();
        if (key.toLowerCase() === 'label') labelCount++;
        if (metadata[key]) {
          // If same key appears multiple times, convert to array to preserve values (except label)
          if (key.toLowerCase() !== 'label') {
            if (!Array.isArray(metadata[key])) metadata[key] = [metadata[key]];
            metadata[key].push(val);
          } else {
            // queue it for validation
            // but store last seen for metadata key
            metadata[key] = val;
          }
        } else {
          metadata[key] = val;
        }
      }

      if (labelCount > 1) {
        const msg = `Multiple 'label:' entries found for statement at index ${idx}. Labels must be unique.`;
        logger.error(msg);
        throw new Error(msg);
      }
      /* ------------------------- end metadata parsing ------------------------- */

      // capture variables of form {{var}}
      const variableMatches = [];
      const varRe = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}/g;
      let vm;
      while ((vm = varRe.exec(normalizedSql)) !== null) {
        const rawVar = vm[0];
        const name = vm[1];
        const parts = name.split('.');
        variableMatches.push({ raw: rawVar, name, parts, lowerParts: parts.map(p => p.toLowerCase()) });
      }

      let tables = [], outCols = [];
      if (st.astNode) {
        try {
          tables = extractTablesFromStmt(st.astNode);
          if (st.astNode.type === 'select') outCols = extractSelectColumnsFromStmt(st.astNode);
        } catch (e) { logger.debug('Error extracting columns/tables from AST: ' + (e && e.message)); }
      } else {
        const fromRe = /(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_\.]*)/gi;
        let m;
        while ((m = fromRe.exec(sql)) !== null) tables.push(m[1].split('.').pop().toLowerCase());
        const selectMatch = sql.match(/SELECT\s+(.*?)\s+FROM/ims);
        if (selectMatch) {
          const parts = selectMatch[1].split(',');
          outCols = parts.map(p => {
            const a = p.trim().split(/\s+AS\s+/i);
            const last = a[a.length - 1].trim();
            const nm = last.match(/(\w+)$/);
            return nm ? nm[1] : null;
          }).filter(Boolean);
        }
      }

      return {
        index: idx,
        sql: normalizedSql,
        originalSql: st.sql,
        start: st.start,
        end: st.end,
        astNode: st.astNode,
        variables: variableMatches,
        tables: Array.from(new Set(tables)),
        outputColumns: outCols,
        dependencies: [], // filled next
        metadata // includes label if present as metadata.label
      };
    });

    this._buildDependencyGraph();
    return { queries: this.queries, dependencies: this.dependencies, executionOrder: this._topologicalSort() };
  }

  _buildDependencyGraph() {
    this.dependencies.clear();
    for (let i = 0; i < this.queries.length; i++) {
      const q = this.queries[i];
      const deps = new Set();

      // For each variable that is not an explicit step reference, try to find producing query
      for (const v of q.variables) {
        // Explicit step reference like query_N.*
        if (v.parts.length > 1 && /^query_\d+$/i.test(v.parts[0])) {
          const stepIdx = parseInt(v.parts[0].split('_')[1], 10) - 1;
          if (!isNaN(stepIdx) && stepIdx >= 0 && stepIdx < i) {
            deps.add(stepIdx);
            continue;
          }
        }

        // search backwards for a column with that name (case-insensitive)
        let found = false;
        const lookFor = v.parts[v.parts.length - 1].toLowerCase();
        for (let j = i - 1; j >= 0; j--) {
          const prev = this.queries[j];
          if (prev.outputColumns && prev.outputColumns.some(c => c && c.toLowerCase() === lookFor)) {
            deps.add(j);
            found = true;
            break;
          }
        }

        // heuristic: if variable 'id' exists and no deps found, map to nearest previous id
        if (!found && lookFor === 'id') {
          for (let j = i - 1; j >= 0; j--) {
            const prev = this.queries[j];
            if (prev.outputColumns && prev.outputColumns.some(c => c && c.toLowerCase() === 'id')) {
              deps.add(j);
              break;
            }
          }
        }
      }

      this.dependencies.set(i, Array.from(deps));
      q.dependencies = Array.from(deps);
    }
  }

  _topologicalSort() {
    const result = [];
    const visited = new Set();
    const visiting = new Set();

    const visit = (n) => {
      if (visited.has(n)) return;
      if (visiting.has(n)) throw new Error('Circular dependency involving query ' + n);
      visiting.add(n);
      const deps = this.dependencies.get(n) || [];
      for (const d of deps) visit(d);
      visiting.delete(n);
      visited.add(n);
      result.push(n);
    };

    for (let i = 0; i < this.queries.length; i++) visit(i);
    return result;
  }

  _arraysEqual(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    if (a.length !== b.length) return false;
    const sa = [...a].sort(), sb = [...b].sort();
    return sa.every((v, i) => v === sb[i]);
  }

  getParallelGroups() {
    const groups = [];
    const sorted = this._topologicalSort();
    const processed = new Set();

    for (const idx of sorted) {
      if (processed.has(idx)) continue;
      const deps = this.dependencies.get(idx) || [];
      const group = [idx];

      for (let j = idx + 1; j < this.queries.length; j++) {
        if (processed.has(j)) continue;
        const otherDeps = this.dependencies.get(j) || [];
        if (this._arraysEqual(deps, otherDeps) && !otherDeps.includes(idx) && !deps.includes(j)) {
          group.push(j);
        }
      }

      group.forEach(i => processed.add(i));
      groups.push(group);
    }

    return groups;
  }

  toWorkflowSteps(targetServer, orchestratorData = {}) {
    const tools = orchestratorData?.tools || [];
    const sqlContent = orchestratorData.sqlContent || "";

    const dbSections = detectDbSections(sqlContent);
    const steps = [];

    const stepNameToIndex = (name) => {
      const m = name.match(/^query_(\d+)$/i);
      if (m) return parseInt(m[1], 10) - 1;
      for (const q of this.queries) {
        if ((q.name && q.name.toLowerCase()) === name.toLowerCase()) return q.index;
      }
      return null;
    };

    for (let i = 0; i < this.queries.length; i++) {
      const q = this.queries[i];
      let sql = q.sql;

      // Generic variable replacement (case-insensitive)
      sql = sql.replace(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}/gi, (match, captured) => {
        const parts = captured.split('.');
        if (parts.length > 1) {
          const stepPart = parts[0];
          const colPart = parts.slice(1).join('.');
          const stepIdx = stepNameToIndex(stepPart);
          if (stepIdx !== null && stepIdx >= 0 && stepIdx < this.queries.length) {
            const depQuery = this.queries[stepIdx];
            const found = (depQuery.outputColumns || []).find(c => c && c.toLowerCase() === colPart.toLowerCase());
            const actualCol = found || colPart;
            if (stepIdx < i && !q.dependencies.includes(stepIdx)) q.dependencies.push(stepIdx);
            return `{{query_${stepIdx + 1}.${actualCol}}}`;
          }
        }

        const lookFor = parts[parts.length - 1].toLowerCase();
        let mappedIdx = null;
        let mappedCol = null;

        for (let j = i - 1; j >= 0; j--) {
          const prev = this.queries[j];
          if (!prev.outputColumns) continue;
          const found = prev.outputColumns.find(c => c && c.toLowerCase() === lookFor);
          if (found) { mappedIdx = j; mappedCol = found; break; }
        }

        if (mappedIdx === null && lookFor === 'id') {
          for (let j = i - 1; j >= 0; j--) {
            const prev = this.queries[j];
            if (!prev.outputColumns) continue;
            const found = prev.outputColumns.find(c => c && c.toLowerCase() === 'id');
            if (found) { mappedIdx = j; mappedCol = found; break; }
          }
        }

        if (mappedIdx !== null) {
          if (!q.dependencies.includes(mappedIdx)) q.dependencies.push(mappedIdx);
          return `{{query_${mappedIdx + 1}.${mappedCol}}}`;
        }

        return match;
      });

      // resolve servers (can be multiple)
      let servers = resolveDbsForPosition(q.start, dbSections, orchestratorData);
      if (servers.length === 0 && tools.length > 0) {
        for (const tool of tools) {
          const label = (tool.data?.label || '').toLowerCase();
          if (q.tables.some(t => label.includes(t.toLowerCase()))) { servers = [tool.id]; break; }
        }
        if (servers.length === 0) servers = [tools[0].id];
      }

      // Create a step for each database
      for (let dbIdx = 0; dbIdx < servers.length; dbIdx++) {
        const stepSuffix = servers.length > 1 ? `_db${dbIdx + 1}` : '';
        const serverTool = tools.find(t => t.id === servers[dbIdx]);
        const databaseName = serverTool?.data?.label || servers[dbIdx];
        
        steps.push({
          name: `query_${i + 1}${stepSuffix}`,
          tool: 'query',
          server: servers[dbIdx],
          params: { sql },
          originalSql: q.originalSql,
          metadata: {
            ...q.metadata,
            tables: q.tables,
            outputColumns: q.outputColumns,
            dependencies: q.dependencies.map(d => `query_${d + 1}`),
            canParallelize: (q.dependencies.length === 0),
            dbIndex: servers.length > 1 ? dbIdx + 1 : undefined,
            database: databaseName
          }
        });
      }
    }

    return steps;
  }

  printDependencyGraph() {
    logger.info('SQL Dependency Graph:');
    for (let i = 0; i < this.queries.length; i++) {
      const q = this.queries[i];
      logger.info(`Query ${i+1}: tables=${q.tables.join(', ')} outputs=${q.outputColumns.join(', ')} vars=${q.variables.map(v=>v.name).join(', ')} deps=${(q.dependencies||[]).map(d=>d+1).join(', ') || 'none'} metadata=${JSON.stringify(q.metadata || {})}`);
    }
  }
}

/* convenience factory */
export function parseSqlFileWithAST(sqlContent, orchestratorData = {}) {
  const p = new SQLASTParser();
  return p.parse(sqlContent, orchestratorData);
}
