"""SQL Orchestrator for executing SQL-based workflows with dependency resolution."""
import re
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional, Set
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

TEMPLATE_VAR_PATTERN = re.compile(r'\{([^}]+)\}')
BUILT_IN_VARS = frozenset({'current_date', 'next_date', 'current_date_time'})


class SQLQuery:
    """Represents a single SQL query with metadata."""
    
    def __init__(
        self,
        query_id: str,
        sql: str,
        label: Optional[str] = None,
        database: Optional[str] = None
    ):
        self.query_id = query_id
        self.sql = sql
        self.label = label or query_id
        self.database = database
        self.dependencies: Set[str] = set()
        
        # Extract dependencies from SQL
        self._extract_dependencies()
    
    def _extract_dependencies(self):
        """Extract variable dependencies from SQL query."""
        for match in TEMPLATE_VAR_PATTERN.finditer(self.sql):
            var_name = match.group(1).strip().split('.')[0]
            if var_name not in BUILT_IN_VARS:
                self.dependencies.add(var_name)


class SQLOrchestrator:
    """Orchestrates execution of multi-step SQL workflows."""
    
    def __init__(self, mcp_manager, query_timeout: int = 60):
        self.mcp_manager = mcp_manager
        self.query_timeout = query_timeout
        self.queries: List[SQLQuery] = []
        self.results: Dict[str, Any] = {}
        now_utc = datetime.now(timezone.utc)
        self.variables: Dict[str, Any] = {
            'current_date': now_utc.strftime('%Y-%m-%d'),
            'next_date': (now_utc + timedelta(days=1)).strftime('%Y-%m-%d'),
            'current_date_time': now_utc.isoformat().replace('+00:00', 'Z')
        }
    
    @staticmethod
    def _extract_metadata(stripped_line: str):
        """Extract label or db metadata from a SQL comment line. Returns (key, value) or None."""
        for prefix in ('-- label:', '--label:'):
            if stripped_line.startswith(prefix):
                return 'label', stripped_line.split(':', 1)[1].strip()
        for prefix in ('-- db:', '--db:'):
            if stripped_line.startswith(prefix):
                return 'db', stripped_line.split(':', 1)[1].strip()
        return None
    
    def _build_query(self, sql_lines, label, database, counter):
        """Build a SQLQuery from accumulated lines."""
        sql_text = '\n'.join(sql_lines).strip()
        if not sql_text:
            return None
        query_id = f"query_{counter}"
        return SQLQuery(
            query_id=query_id,
            sql=sql_text,
            label=label or query_id,
            database=database
        )
    
    def parse_sql_file(self, sql_content: str) -> List[SQLQuery]:
        """
        Parse SQL file content into individual queries.
        
        Expects SQL comments for metadata:
        -- label: Query Name
        -- db: database-name
        
        Queries are separated by semicolons.
        """
        queries = []
        current_sql = []
        metadata = {'label': None, 'db': None}
        query_counter = 1
        
        for line in sql_content.split('\n'):
            stripped = line.strip()
            
            meta = self._extract_metadata(stripped)
            if meta:
                metadata[meta[0]] = meta[1]
                continue
            
            if not stripped or stripped.startswith('--'):
                continue
            
            current_sql.append(line)
            
            if stripped.endswith(';'):
                query = self._build_query(current_sql, metadata['label'], metadata['db'], query_counter)
                if query:
                    queries.append(query)
                    query_counter += 1
                current_sql = []
                metadata['label'] = None
        
        # Handle last query if no semicolon
        query = self._build_query(current_sql, metadata['label'], metadata['db'], query_counter)
        if query:
            queries.append(query)
        
        logger.info(f"Parsed {len(queries)} SQL queries from file")
        return queries
    
    def _get_var_lower_map(self) -> Dict[str, str]:
        """Build lowercase → original-case key map for case-insensitive lookup."""
        return {k.lower(): k for k in self.variables}
    
    def _has_variable(self, name: str) -> bool:
        """Case-insensitive check if a variable exists."""
        return name.lower() in self._get_var_lower_map()
    
    def _resolve_path(self, parts: List[str]) -> Any:
        """Resolve a dotted variable path from variables or step results."""
        var_lower_map = self._get_var_lower_map()
        base_key = var_lower_map.get(parts[0].lower())
        
        if base_key:
            value = self._unwrap_array(self.variables[base_key])
        elif parts[0] in self.results:
            value = self._unwrap_array(self.results[parts[0]])
        else:
            return None  # sentinel: not found
        
        for part in parts[1:]:
            value = self._unwrap_array(value)
            if value is None:
                return None
            if not isinstance(value, dict):
                logger.warning(f"Cannot navigate path {'.' .join(parts)}, expected dict got {type(value)}")
                return None
            # Case-insensitive column lookup
            col_key = next((k for k in value if k.lower() == part.lower()), None)
            if col_key is None:
                logger.warning(f"Column '{part}' not found in {parts[0]}")
                return None
            value = value[col_key]
        
        return self._unwrap_array(value)
    
    def resolve_variables(self, sql: str) -> str:
        """Replace template variables in SQL with actual values."""
        def replacer(match):
            var_path = match.group(1).strip()
            parts = var_path.split('.')
            value = self._resolve_path(parts)
            if value is None:
                logger.warning(f"Variable '{var_path}' resolved to NULL")
                return 'NULL'
            return self._format_sql_value(value)
        
        return TEMPLATE_VAR_PATTERN.sub(replacer, sql)
    
    def _unwrap_array(self, value: Any) -> Any:
        """Unwrap single-element arrays recursively (Node.js behavior)."""
        if isinstance(value, list):
            if len(value) == 0:
                return None
            if len(value) == 1:
                return value[0]
        return value
    
    def _format_sql_value(self, value: Any) -> str:
        """Format a Python value for SQL interpolation."""
        if value is None:
            return 'NULL'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            # Escape single quotes
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        # For complex types, use NULL
        logger.warning(f"Cannot format complex value for SQL: {type(value)}")
        return 'NULL'
    
    async def execute_query(
        self,
        query: SQLQuery,
        server_id: str,
    ) -> Dict[str, Any]:
        """Execute a single SQL query on an MCP server."""
        try:
            # Resolve template variables
            resolved_sql = self.resolve_variables(query.sql)
            
            logger.info(f"Executing query: {query.label} on database: {server_id}")
            logger.debug(f"SQL: {resolved_sql[:200]}...")
            
            # Execute via MCP with timeout context manager
            async with asyncio.timeout(self.query_timeout):
                result = await self.mcp_manager.execute_tool(
                    server_id=server_id,
                    tool_name='query',
                    arguments={'sql': resolved_sql},
                )
            
            if result.get('success'):
                # Parse result content
                content = result.get('content', [])
                if content and hasattr(content[0], 'text'):
                    result_data = json.loads(content[0].text)
                else:
                    result_data = content
                
                # Store result
                self.results[query.query_id] = result_data
                
                # Auto-promote single-row, single-column results to base variables
                if isinstance(result_data, list) and len(result_data) == 1:
                    row = result_data[0]
                    if isinstance(row, dict) and len(row) == 1:
                        # Extract the single column name and value
                        col_name = list(row.keys())[0]
                        col_value = row[col_name]
                        # Add to variables (preserve original case)
                        self.variables[col_name] = col_value
                        logger.info(f"Auto-promoted variable: {col_name} = {col_value}")
                
                return {
                    'query_id': query.query_id,
                    'label': query.label,
                    'success': True,
                    'result': result_data,
                    'database': server_id  # Actual database used
                }
            else:
                return {
                    'query_id': query.query_id,
                    'label': query.label,
                    'success': False,
                    'error': result.get('error', 'Unknown error')
                }
                
        except Exception as e:
            logger.error(f"Query execution failed: {query.label} - {e}")
            return {
                'query_id': query.query_id,
                'label': query.label,
                'success': False,
                'error': str(e)
            }
    
    def _find_ready_queries(self, remaining, executed_ids):
        """Find queries whose dependencies are all satisfied."""
        return [
            q for q in remaining
            if all(
                dep in executed_ids or self._has_variable(dep)
                for dep in q.dependencies
            )
        ]
    
    def _resolve_server_id(self, query, default_server_id):
        """Resolve the server ID for a query based on database mapping."""
        if query.database and self.db_server_map:
            mapped_id = self.db_server_map.get(query.database)
            if mapped_id:
                return mapped_id
            logger.warning(f"No server mapping for database '{query.database}', using default")
        return default_server_id
    
    async def execute_workflow(
        self,
        sql_content: str,
        server_id: str,
        db_server_map: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Execute complete SQL workflow with dynamic dependency resolution.
        
        Args:
            sql_content: SQL file content
            server_id: Default MCP server ID for database
            db_server_map: Mapping of database labels to MCP server IDs
        
        Returns:
            Execution results
        """
        self.db_server_map = db_server_map or {}
        try:
            self.queries = self.parse_sql_file(sql_content)
            
            if not self.queries:
                return {'success': False, 'error': 'No queries found in SQL content'}
            
            remaining = self.queries.copy()
            executed_ids = set()
            all_results = []
            iteration = 0
            
            while remaining:
                iteration += 1
                logger.info(f"Iteration {iteration}: {len(remaining)} queries remaining")
                
                ready = self._find_ready_queries(remaining, executed_ids)
                
                if not ready:
                    pending_deps = {
                        dep for q in remaining for dep in q.dependencies
                        if dep not in executed_ids and not self._has_variable(dep)
                    }
                    return {
                        'success': False,
                        'error': f"Cannot resolve dependencies. Queries waiting: {[q.query_id for q in remaining]}, Missing dependencies: {pending_deps}",
                        'results': all_results,
                        'queries_executed': len(all_results),
                        'failures': sum(1 for r in all_results if not r.get('success'))
                    }
                
                for query in ready:
                    query_server_id = self._resolve_server_id(query, server_id)
                    logger.info(f"Executing: {query.label} ({query.query_id}) on {query.database or 'default'}")
                    result = await self.execute_query(query, query_server_id)
                    all_results.append(result)
                    executed_ids.add(query.query_id)
                    remaining.remove(query)
            
            failed = [r for r in all_results if not r.get('success')]
            
            return {
                'success': len(failed) == 0,
                'results': all_results,
                'queries_executed': len(all_results),
                'failures': len(failed)
            }
            
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
