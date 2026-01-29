"""CloudWatch logs tools for the agent."""

import boto3
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from strands import tool
from config import get_aws_config

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CloudWatchClient:
    """Client for interacting with AWS CloudWatch Logs."""
    
    def __init__(self):
        """Initialize the CloudWatch Logs client."""
        aws_config = get_aws_config()
        
        # Ensure AWS credentials are set in environment variables
        os.environ['AWS_ACCESS_KEY_ID'] = aws_config.get('aws_access_key_id', '')
        os.environ['AWS_SECRET_ACCESS_KEY'] = aws_config.get('aws_secret_access_key', '')
        os.environ['AWS_REGION'] = aws_config.get('region_name', 'us-west-2')
        
        logger.info(f"Initializing CloudWatch client with region: {aws_config.get('region_name')}")
        self.client = boto3.client('logs', **aws_config)
    
    def list_log_groups(self) -> List[str]:
        """List all available CloudWatch log groups."""
        try:
            logger.info("Listing CloudWatch log groups")
            logger.info(f"AWS Region: {os.environ.get('AWS_REGION', 'Not set')}")
            logger.info(f"AWS Access Key ID: {os.environ.get('AWS_ACCESS_KEY_ID', 'Not set')[:5]}..." if os.environ.get('AWS_ACCESS_KEY_ID') else "AWS Access Key ID: Not set")
            
            response = self.client.describe_log_groups()
            log_groups = [log_group['logGroupName'] for log_group in response.get('logGroups', [])]
            logger.info(f"Found {len(log_groups)} log groups")
            return log_groups
        except Exception as e:
            logger.error(f"Error listing log groups: {e}")
            return []
    
    def get_logs(self, 
                log_group_name: str, 
                start_time: Optional[datetime] = None,
                end_time: Optional[datetime] = None,
                filter_pattern: str = "",
                limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get logs from a CloudWatch log group.
        
        Args:
            log_group_name: Name of the log group
            start_time: Start time for logs (default: 1 hour ago)
            end_time: End time for logs (default: now)
            filter_pattern: Filter pattern for logs
            limit: Maximum number of log events to return
            
        Returns:
            List of log events
        """
        # Validate log group name
        if not log_group_name or not log_group_name.strip():
            logger.error("Empty log group name provided")
            return []
            
        if start_time is None:
            start_time = datetime.now() - timedelta(hours=1)
        
        if end_time is None:
            end_time = datetime.now()
        
        # Convert to milliseconds since epoch
        start_time_ms = int(start_time.timestamp() * 1000)
        end_time_ms = int(end_time.timestamp() * 1000)
        
        logger.info(f"Fetching logs from {log_group_name} with filter: '{filter_pattern}'")
        logger.info(f"Time range: {start_time.isoformat()} to {end_time.isoformat()}")
        logger.info(f"AWS Region: {os.environ.get('AWS_REGION', 'Not set')}")
        logger.info(f"AWS Access Key ID: {os.environ.get('AWS_ACCESS_KEY_ID', 'Not set')[:5]}..." if os.environ.get('AWS_ACCESS_KEY_ID') else "AWS Access Key ID: Not set")
        
        # First check if the log group exists
        try:
            log_groups = self.list_log_groups()
            if log_group_name not in log_groups:
                logger.warning(f"Log group {log_group_name} does not exist")
                return []
        except Exception as e:
            logger.error(f"Error checking if log group exists: {e}")
            # Continue anyway, as the log group might exist but we don't have permission to list all groups
        
        # Try a simpler query first to verify access
        try:
            test_response = self.client.describe_log_streams(
                logGroupName=log_group_name,
                limit=1
            )
            logger.info(f"Successfully accessed log group: {log_group_name}")
            
            # Check if there are any log streams
            if not test_response.get('logStreams', []):
                logger.warning(f"No log streams found in log group {log_group_name}")
                return []
        except Exception as e:
            logger.error(f"Error accessing log group {log_group_name}: {e}")
            return []
        
        kwargs = {
            'logGroupName': log_group_name,
            'startTime': start_time_ms,
            'endTime': end_time_ms,
            'limit': limit
        }
        
        # Process filter pattern to handle commas and special characters
        if filter_pattern:
            # Clean and validate the filter pattern
            cleaned_filter = self._clean_filter_pattern(filter_pattern)
            logger.info(f"Using filter pattern: '{cleaned_filter}'")
            kwargs['filterPattern'] = cleaned_filter
        
        try:
            # Verify AWS credentials before making the API call
            self._verify_aws_credentials()
            
            # Try with a higher limit first to ensure we get logs if they exist
            kwargs['limit'] = min(1000, limit * 10)  # Increase limit but cap at 1000
            
            response = self.client.filter_log_events(**kwargs)
            events = response.get('events', [])
            logger.info(f"Retrieved {len(events)} log events from {log_group_name}")
            
            if not events:
                # If no logs found, try without filter pattern
                if 'filterPattern' in kwargs:
                    logger.warning(f"No logs found with filter pattern. Trying without filter...")
                    del kwargs['filterPattern']
                    response = self.client.filter_log_events(**kwargs)
                    events = response.get('events', [])
                    logger.info(f"Retrieved {len(events)} log events without filter")
                
                # If still no logs, try with a wider time range
                if not events:
                    logger.warning(f"No logs found in specified time range. Trying with wider time range...")
                    # Try with a 24-hour time range
                    wider_start_time = datetime.now() - timedelta(hours=24)
                    kwargs['startTime'] = int(wider_start_time.timestamp() * 1000)
                    response = self.client.filter_log_events(**kwargs)
                    events = response.get('events', [])
                    logger.info(f"Retrieved {len(events)} log events with wider time range")
                    
                    # If logs found with wider time range, inform the user
                    if events:
                        logger.info(f"Found logs in wider time range ({wider_start_time.isoformat()} to {end_time.isoformat()})")
            
            if not events:
                logger.warning(f"No log events found in {log_group_name} for any time range or filter")
            
            return events
        except Exception as e:
            logger.error(f"Error fetching logs from {log_group_name}: {e}")
            # If there's an error with the filter pattern, try again without it
            if 'filterPattern' in kwargs and 'InvalidParameterException' in str(e):
                logger.warning(f"Invalid filter pattern: '{filter_pattern}'. Trying without filter.")
                del kwargs['filterPattern']
                try:
                    response = self.client.filter_log_events(**kwargs)
                    events = response.get('events', [])
                    logger.info(f"Retrieved {len(events)} log events without filter")
                    return events
                except Exception as e2:
                    logger.error(f"Error fetching logs without filter: {e2}")
                    return []
            return []
    
    def _clean_filter_pattern(self, filter_pattern: str) -> str:
        """
        Clean and validate a CloudWatch Logs filter pattern.
        
        Args:
            filter_pattern: The raw filter pattern
            
        Returns:
            A cleaned filter pattern that's safe to use with CloudWatch Logs
        """
        # If the pattern contains commas, it might need to be quoted
        if ',' in filter_pattern and not (filter_pattern.startswith('"') and filter_pattern.endswith('"')):
            # Check if it's already properly formatted for multiple terms
            if not (filter_pattern.startswith('{') and filter_pattern.endswith('}')):
                # Split by commas and create a proper filter expression
                terms = [term.strip() for term in filter_pattern.split(',')]
                return ' OR '.join(terms)
        
        return filter_pattern
    
    def _verify_aws_credentials(self):
        """Verify that AWS credentials are properly set."""
        # Check environment variables
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        region = os.environ.get('AWS_REGION')
        
        if not access_key or not secret_key:
            logger.warning("AWS credentials not found in environment variables")
            
            # Try to get from boto3 session
            session = boto3.Session()
            credentials = session.get_credentials()
            
            if credentials:
                logger.info("Using AWS credentials from boto3 session")
            else:
                logger.error("No AWS credentials found. CloudWatch operations will likely fail.")

# Initialize the CloudWatch client
cloudwatch_client = CloudWatchClient()

@tool
def list_cloudwatch_log_groups() -> List[str]:
    """
    List all available CloudWatch log groups.
    
    Returns:
        List of log group names
    """
    # Force reinitialization to ensure fresh credentials
    global cloudwatch_client
    cloudwatch_client = CloudWatchClient()
    return cloudwatch_client.list_log_groups()

@tool
def get_cloudwatch_logs(log_group_name: str, 
                        hours_ago: int = 1,
                        filter_pattern: str = "",
                        limit: int = 100) -> str:
    """
    Get logs from a CloudWatch log group.
    
    Args:
        log_group_name: Name of the log group
        hours_ago: Number of hours to look back for logs
        filter_pattern: Filter pattern for logs (e.g., "ERROR", "Exception")
                       For multiple patterns, use "ERROR OR Exception"
        limit: Maximum number of log events to return
        
    Returns:
        JSON string containing log events
    """
    # Reinitialize the CloudWatch client to ensure fresh credentials
    global cloudwatch_client
    cloudwatch_client = CloudWatchClient()
    
    start_time = datetime.now() - timedelta(hours=hours_ago)
    end_time = datetime.now()
    
    logs = cloudwatch_client.get_logs(
        log_group_name=log_group_name,
        start_time=start_time,
        end_time=end_time,
        filter_pattern=filter_pattern,
        limit=limit
    )
    
    # Format logs for better readability
    formatted_logs = []
    for log in logs:
        formatted_logs.append({
            'timestamp': datetime.fromtimestamp(log['timestamp'] / 1000).isoformat(),
            'message': log['message'],
            'logStreamName': log.get('logStreamName', '')
        })
    
    # Log the result
    if not formatted_logs:
        logger.info(f"No logs found for {log_group_name} in the past {hours_ago} hours")
        # Add a special marker to indicate no logs were found (not an error)
        return json.dumps({
            "status": "NO_LOGS_FOUND",
            "message": f"No logs found for {log_group_name} in the past {hours_ago} hours",
            "logs": []
        }, indent=2)
    else:
        logger.info(f"Formatted {len(formatted_logs)} log events for {log_group_name}")
        return json.dumps(formatted_logs, indent=2)

@tool
def analyze_logs_for_errors(logs_json: str) -> str:
    """
    Analyze logs to identify errors and issues.
    
    Args:
        logs_json: JSON string containing log events
        
    Returns:
        JSON string containing identified errors and issues
    """
    # This is a placeholder for the agent to use its reasoning capabilities
    # The agent will parse the logs and identify errors based on patterns
    return logs_json