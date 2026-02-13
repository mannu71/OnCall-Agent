"""SQL Orchestrator for executing SQL-based workflows with dependency resolution."""
import re
import asyncio
import logging
from typing import Dict, List, Any, Optional, Set, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Pattern to match template variables like {variable_name} or {step.column}
TEMPLATE_VAR_PATTERN = re.compile(r'\{([^}]+)\}')


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
        self.output_columns: List[str] = []
        
        # Extract dependencies from SQL
        self._extract_dependencies()
    
    def _extract_dependencies(self):
        """Extract variable dependencies from SQL query."""
        for match in TEMPLATE_VAR_PATTERN.finditer(self.sql):
            var_path = match.group(1).strip()
            # Extract the step name (before the first dot if exists)
            parts = var_path.split('.')
            if parts[0] not in ['current_date', 'next_date', 'current_date_time']:
                self.dependencies.add(parts[0])
    
    def can_parallelize_with(self, other: 'SQLQuery') -> bool:
        """Check if this query can run in parallel with another."""
        # Can run in parallel if no dependencies on each other
        return (
            self.query_id not in other.dependencies and
            other.query_id not in self.dependencies
        )


class SQLOrchestrator:
    """Orchestrates execution of multi-step SQL workflows."""
    
    def __init__(self, mcp_manager):
        self.mcp_manager = mcp_manager
        self.queries: List[SQLQuery] = []
        self.results: Dict[str, Any] = {}
        self.variables: Dict[str, Any] = {
            'current_date': datetime.utcnow().strftime('%Y-%m-%d'),
            'next_date': (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d'),
            'current_date_time': datetime.utcnow().isoformat() + 'Z'
        }
    
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
        current_label = None
        current_db = None
        query_counter = 1
        
        lines = sql_content.split('\n')
        
        for line in lines:
            stripped = line.strip()
            
            # Extract metadata from comments
            if stripped.startswith('-- label:') or stripped.startswith('--label:'):
                current_label = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('-- db:') or stripped.startswith('--db:'):
                current_db = stripped.split(':', 1)[1].strip()
            elif stripped and not stripped.startswith('--'):
                current_sql.append(line)
                
                # Check for query terminator
                if stripped.endswith(';'):
                    sql_text = '\n'.join(current_sql).strip()
                    if sql_text:
                        query_id = f"query_{query_counter}"
                        query = SQLQuery(
                            query_id=query_id,
                            sql=sql_text,
                            label=current_label or query_id,
                            database=current_db
                        )
                        queries.append(query)
                        query_counter += 1
                    
                    # Reset for next query
                    current_sql = []
                    current_label = None
                    # Keep current_db for subsequent queries
        
        # Handle last query if no semicolon
        if current_sql:
            sql_text = '\n'.join(current_sql).strip()
            if sql_text:
                query_id = f"query_{query_counter}"
                query = SQLQuery(
                    query_id=query_id,
                    sql=sql_text,
                    label=current_label or query_id,
                    database=current_db
                )
                queries.append(query)
        
        logger.info(f"Parsed {len(queries)} SQL queries from file")
        return queries
    
    def get_execution_order(self, queries: List[SQLQuery]) -> List[List[SQLQuery]]:
        """
        Determine execution order with parallel groups.
        
        Returns:
            List of query groups where each group can be executed in parallel
        """
        remaining = queries.copy()
        execution_groups = []
        executed_ids = set()
        
        while remaining:
            # Find queries that can execute now (dependencies met)
            ready = []
            for query in remaining:
                if query.dependencies.issubset(executed_ids):
                    ready.append(query)
            
            if not ready:
                # Circular dependency or missing dependency
                pending_deps = set()
                for query in remaining:
                    pending_deps.update(query.dependencies - executed_ids)
                raise ValueError(
                    f"Cannot resolve dependencies. Queries waiting: {[q.query_id for q in remaining]}, "
                    f"Missing dependencies: {pending_deps}"
                )
            
            # Group ready queries by parallelizability
            parallel_groups = []
            for query in ready:
                placed = False
                for group in parallel_groups:
                    if all(query.can_parallelize_with(q) for q in group):
                        group.append(query)
                        placed = True
                        break
                if not placed:
                    parallel_groups.append([query])
            
            # Add groups to execution order
            for group in parallel_groups:
                execution_groups.append(group)
                for query in group:
                    executed_ids.add(query.query_id)
                    remaining.remove(query)
        
        logger.info(f"Execution plan: {len(execution_groups)} groups")
        for i, group in enumerate(execution_groups):
            logger.info(f"  Group {i+1}: {[q.label for q in group]}")
        
        return execution_groups
    
    def resolve_variables(self, sql: str) -> str:
        """Replace template variables in SQL with actual values."""
        def replacer(match):
            var_path = match.group(1).strip()
            parts = var_path.split('.')
            
            # Check for system variables first
            if parts[0] in self.variables:
                value = self.variables[parts[0]]
                return self._format_sql_value(value)
            
            # Check step results
            if parts[0] in self.results:
                result = self.results[parts[0]]
                
                # Navigate nested path
                for part in parts[1:]:
                    if isinstance(result, list) and result:
                        result = result[0]  # Take first row
                    if isinstance(result, dict):
                        # Case-insensitive column lookup
                        matching_key = next(
                            (k for k in result.keys() if k.lower() == part.lower()),
                            None
                        )
                        if matching_key:
                            result = result[matching_key]
                        else:
                            logger.warning(f"Column '{part}' not found in {parts[0]}")
                            return 'NULL'
                    else:
                        logger.warning(f"Cannot navigate to {var_path}")
                        return 'NULL'
                
                return self._format_sql_value (result)
            
            logger.warning(f"Variable '{var_path}' not found")
            return 'NULL'
        
        return TEMPLATE_VAR_PATTERN.sub(replacer, sql)
    
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
        timeout: int = 60
    ) -> Dict[str, Any]:
        """Execute a single SQL query on an MCP server."""
        try:
            # Resolve template variables
            resolved_sql = self.resolve_variables(query.sql)
            
            logger.info(f"Executing query: {query.label}")
            logger.debug(f"SQL: {resolved_sql[:200]}...")
            
            # Execute via MCP
            result = await self.mcp_manager.execute_tool(
                server_id=server_id,
                tool_name='query',  # Standard PostgreSQL MCP tool name
                arguments={'sql': resolved_sql},
                timeout=timeout
            )
            
            if result.get('success'):
                # Parse result content
                content = result.get('content', [])
                if content and hasattr(content[0], 'text'):
                    import json
                    result_data = json.loads(content[0].text)
                else:
                    result_data = content
                
                # Store result
                self.results[query.query_id] = result_data
                
                return {
                    'query_id': query.query_id,
                    'label': query.label,
                    'success': True,
                    'result': result_data,
                    'database': query.database
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
    
    async def execute_workflow(
        self,
        sql_content: str,
        server_id: str,
        timeout: int = 900
    ) -> Dict[str, Any]:
        """
        Execute complete SQL workflow.
        
        Args:
            sql_content: SQL file content
            server_id: MCP server ID for database
            timeout: Per-query timeout in seconds
        
        Returns:
            Execution results
        """
        try:
            # Parse SQL file
            self.queries = self.parse_sql_file(sql_content)
            
            if not self.queries:
                return {
                    'success': False,
                    'error': 'No queries found in SQL content'
                }
            
            # Determine execution order
            execution_groups = self.get_execution_order(self.queries)
            
            # Execute queries
            all_results = []
            
            for group_idx, group in enumerate(execution_groups):
                logger.info(f"Executing group {group_idx + 1}/{len(execution_groups)}")
                
                if len(group) == 1:
                    # Sequential execution
                    result = await self.execute_query(group[0], server_id, timeout)
                    all_results.append(result)
                else:
                    # Parallel execution
                    logger.info(f"Executing {len(group)} queries in parallel")
                    tasks = [
                        self.execute_query(query, server_id, timeout)
                        for query in group
                    ]
                    group_results = await asyncio.gather(*tasks)
                    all_results.extend(group_results)
            
            # Check for failures
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
