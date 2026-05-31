"""Load SQL content for orchestrator nodes from inline text or workflow files."""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def load_sql_content(
    node_data: Dict[str, Any],
    workflow_name: Optional[str] = None,
) -> Optional[str]:
    """Load SQL content from node data, either inline or from a file."""
    sql_content = node_data.get('sqlContent') or node_data.get('fileContent')
    if sql_content:
        return sql_content

    sql_file = node_data.get('sqlFile') or node_data.get('fileName')
    if not sql_file:
        return None

    sql_filename = sql_file.replace('sql/', '').replace('sql\\', '')

    search_paths = []

    if workflow_name:
        safe_name = workflow_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        search_paths.extend([
            Path('data/storage/workflows') / safe_name / sql_filename,
            Path('/app/data/storage/workflows') / safe_name / sql_filename,
        ])

    search_paths.extend([
        Path('data/config/sql') / sql_filename,
        Path('/app/data/config/sql') / sql_filename,
    ])

    for candidate in search_paths:
        if candidate.exists():
            logger.info("Loaded SQL from: %s", candidate)
            return candidate.read_text(encoding='utf-8')

    return None


def parse_time_range_minutes(time_range: str) -> int:
    """Convert time range string (e.g. '15m', '1h', '7d') to minutes."""
    units = {'m': 1, 'h': 60, 'd': 1440}
    if not time_range:
        return 60
    suffix = time_range[-1]
    if suffix not in units:
        return 60
    try:
        value = int(time_range[:-1])
        return value * units[suffix]
    except (ValueError, IndexError):
        return 60
