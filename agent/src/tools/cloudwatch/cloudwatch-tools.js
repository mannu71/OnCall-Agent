/**
 * CloudWatch Logs Tools for the Agent
 * 
 * This module provides tools for interacting with AWS CloudWatch Logs
 * using the Strands Agents SDK pattern.
 */

import { createMCPClient } from '../../worker/mcp-client.js';
import logger from '../../shared/logger.js';

/**
 * CloudWatch Client wrapper for MCP-based CloudWatch operations
 */
class CloudWatchClient {
    constructor() {
        this.client = null;
        this.connected = false;
    }

    /**
     * Initialize and connect to the CloudWatch MCP server
     */
    async connect() {
        if (this.connected) {
            return;
        }

        try {
            logger.info('Initializing CloudWatch MCP client');
            
            const serverConfig = {
                label: 'aws-cloudwatch',
                mcpConfig: {
                    type: 'stdio',
                    command: 'uvx',
                    args: [
                        '--native-tls',
                        'awslabs.cloudwatch-mcp-server@latest'
                    ],
                    env: {
                        AWS_PROFILE: process.env.AWS_PROFILE || 'test-dev',
                        AWS_REGION: process.env.AWS_REGION || 'eu-west-1',
                        FASTMCP_LOG_LEVEL: 'ERROR'
                    }
                }
            };

            // Add AWS credentials if provided in environment
            if (process.env.AWS_ACCESS_KEY_ID) {
                serverConfig.mcpConfig.env.AWS_ACCESS_KEY_ID = process.env.AWS_ACCESS_KEY_ID;
            }
            if (process.env.AWS_SECRET_ACCESS_KEY) {
                serverConfig.mcpConfig.env.AWS_SECRET_ACCESS_KEY = process.env.AWS_SECRET_ACCESS_KEY;
            }

            this.client = createMCPClient(serverConfig);
            await this.client.connect();
            this.connected = true;
            
            logger.info(`CloudWatch client connected with region: ${serverConfig.mcpConfig.env.AWS_REGION}`);
        } catch (error) {
            logger.error(`Error connecting to CloudWatch MCP server: ${error.message}`);
            throw error;
        }
    }

    /**
     * Disconnect from the CloudWatch MCP server
     */
    async disconnect() {
        if (this.client && this.connected) {
            await this.client.disconnect();
            this.connected = false;
            logger.info('CloudWatch client disconnected');
        }
    }

    /**
     * List all available CloudWatch log groups
     * @returns {Promise<Array<string>>} List of log group names
     */
    async listLogGroups() {
        await this.connect();
        
        try {
            logger.info('Listing CloudWatch log groups');
            
            const result = await this.client.callTool('describe_log_groups', {
                limit: 50
            });

            // Parse the result to extract log group names
            const logGroups = this._parseLogGroupsResult(result);
            logger.info(`Found ${logGroups.length} log groups`);
            
            return logGroups;
        } catch (error) {
            logger.error(`Error listing log groups: ${error.message}`);
            return [];
        }
    }

    /**
     * Get logs from a CloudWatch log group using CloudWatch Logs Insights
     * Based on AWS CloudWatch MCP Server documentation
     * @param {Object} options - Query options
     * @param {string} options.logGroupName - Name of the log group
     * @param {number} options.hoursAgo - Number of hours to look back (default: 1)
     * @param {string} options.filterPattern - Filter pattern for logs (optional)
     * @param {number} options.limit - Maximum number of log events to return (default: 100)
     * @returns {Promise<Array<Object>>} List of log events
     */
    async getLogs({ logGroupName, hoursAgo = 1, filterPattern = '', limit = 100 }) {
        await this.connect();

        // Validate log group name
        if (!logGroupName || !logGroupName.trim()) {
            logger.error('Empty log group name provided');
            return [];
        }

        try {
            // Build CloudWatch Logs Insights query
            let queryString = `fields @timestamp, @message, @logStream | sort @timestamp desc | limit ${limit}`;
            
            if (filterPattern) {
                // Add filter to the query
                queryString = `fields @timestamp, @message, @logStream | filter @message like /${filterPattern}/i | sort @timestamp desc | limit ${limit}`;
            }

            logger.info(`Fetching logs from ${logGroupName}`);
            logger.info(`Query: ${queryString}`);
            logger.info(`Time range: Last ${hoursAgo} hour(s)`);

            // Use CloudWatch Logs Insights query
            const result = await this.executeLogsInsightsQuery({
                logGroupName,
                queryString,
                hoursAgo
            });

            logger.info(`Retrieved ${result.length} log events`);

            // If no events found, try with wider time range
            if (result.length === 0 && hoursAgo < 24) {
                logger.warn('No logs found in specified time range. Trying with wider time range (24 hours)...');
                const widerResult = await this.executeLogsInsightsQuery({
                    logGroupName,
                    queryString,
                    hoursAgo: 24
                });
                logger.info(`Retrieved ${widerResult.length} events with wider time range`);
                return widerResult;
            }

            return result;
        } catch (error) {
            logger.error(`Error fetching logs from ${logGroupName}: ${error.message}`);
            return [];
        }
    }

    /**
     * Execute a CloudWatch Logs Insights query
     * @param {Object} options - Query options
     * @param {string} options.logGroupName - Name of the log group
     * @param {string} options.queryString - CloudWatch Logs Insights query string
     * @param {number} options.hoursAgo - Number of hours to look back (default: 1)
     * @returns {Promise<Array<Object>>} Query results
     */
    async executeLogsInsightsQuery({ logGroupName, queryString, hoursAgo = 1 }) {
        await this.connect();

        try {
            const endTime = new Date();
            const startTime = new Date(endTime.getTime() - (hoursAgo * 60 * 60 * 1000));

            logger.info(`Executing Logs Insights query on ${logGroupName}`);
            
            // Start the query
            const startParams = {
                log_group_names: [logGroupName],
                query_string: queryString,
                start_time: startTime.toISOString(),
                end_time: endTime.toISOString()
            };

            const startResult = await this.client.callTool('execute_log_insights_query', startParams);
            const queryId = this._extractQueryId(startResult);

            if (!queryId) {
                logger.error('Failed to get query ID from execute_log_insights_query');
                return [];
            }

            logger.info(`Query started with ID: ${queryId}`);

            // Poll for results
            let attempts = 0;
            const maxAttempts = 30; // 30 seconds max wait
            
            while (attempts < maxAttempts) {
                await new Promise(resolve => setTimeout(resolve, 1000)); // Wait 1 second
                
                const resultsParams = { query_id: queryId };
                const resultsResponse = await this.client.callTool('get_logs_insight_query_results', resultsParams);
                
                const status = this._extractQueryStatus(resultsResponse);
                
                if (status === 'Complete') {
                    const results = this._parseLogsInsightsResults(resultsResponse);
                    logger.info(`Query completed with ${results.length} results`);
                    return results;
                } else if (status === 'Failed' || status === 'Cancelled') {
                    logger.error(`Query ${status.toLowerCase()}`);
                    return [];
                }
                
                attempts++;
                logger.info(`Query status: ${status}, waiting... (${attempts}/${maxAttempts})`);
            }

            logger.warn('Query timed out, cancelling...');
            await this.client.callTool('cancel_logs_insight_query', { query_id: queryId });
            return [];

        } catch (error) {
            logger.error(`Error executing Logs Insights query: ${error.message}`);
            return [];
        }
    }

    /**
     * Clean and validate a CloudWatch Logs filter pattern
     * @param {string} filterPattern - The raw filter pattern
     * @returns {string} A cleaned filter pattern
     */
    _cleanFilterPattern(filterPattern) {
        // If the pattern contains commas, it might need to be quoted
        if (filterPattern.includes(',') && 
            !filterPattern.startsWith('"') && 
            !filterPattern.endsWith('"')) {
            // Check if it's already properly formatted for multiple terms
            if (!filterPattern.startsWith('{') && !filterPattern.endsWith('}')) {
                // Split by commas and create a proper filter expression
                const terms = filterPattern.split(',').map(term => term.trim());
                return terms.join(' OR ');
            }
        }
        
        return filterPattern;
    }

    /**
     * Parse log groups result from MCP tool response
     * @param {Object} result - MCP tool result
     * @returns {Array<string>} List of log group names
     */
    _parseLogGroupsResult(result) {
        try {
            // The result might be in different formats depending on the MCP server
            if (Array.isArray(result)) {
                return result.map(lg => lg.logGroupName || lg);
            }
            
            // Check for structuredContent (MCP server format)
            if (result.structuredContent && result.structuredContent.log_group_metadata) {
                const logGroups = result.structuredContent.log_group_metadata;
                if (Array.isArray(logGroups)) {
                    return logGroups.map(lg => lg.logGroupName || lg);
                }
            }
            
            if (result.content && Array.isArray(result.content)) {
                const textContent = result.content.find(c => c.type === 'text');
                if (textContent && textContent.text) {
                    // Try to parse as JSON first
                    try {
                        const parsed = JSON.parse(textContent.text);
                        if (Array.isArray(parsed)) {
                            return parsed.map(lg => lg.logGroupName || lg);
                        }
                        if (parsed.logGroups) {
                            return parsed.logGroups.map(lg => lg.logGroupName || lg);
                        }
                        if (parsed.log_group_metadata) {
                            return parsed.log_group_metadata.map(lg => lg.logGroupName || lg);
                        }
                    } catch (jsonError) {
                        // If JSON parsing fails, try to extract log group names from text
                        const logGroupMatches = textContent.text.match(/'logGroupName':\s*'([^']+)'/g);
                        if (logGroupMatches) {
                            return logGroupMatches.map(match => {
                                const nameMatch = match.match(/'logGroupName':\s*'([^']+)'/);
                                return nameMatch ? nameMatch[1] : null;
                            }).filter(Boolean);
                        }
                    }
                }
            }

            if (result.logGroups) {
                return result.logGroups.map(lg => lg.logGroupName || lg);
            }

            return [];
        } catch (error) {
            logger.error(`Error parsing log groups result: ${error.message}`);
            return [];
        }
    }

    /**
     * Parse log sample result from MCP resource response
     * @param {Object} result - MCP resource result
     * @returns {Array<Object>} List of formatted log events
     */
    _parseLogSampleResult(result) {
        try {
            let events = [];

            // Check for structuredContent first
            if (result.structuredContent) {
                const content = result.structuredContent;
                if (content.events && Array.isArray(content.events)) {
                    events = content.events;
                }
            }

            // Fallback to content array
            if (events.length === 0 && result.content && Array.isArray(result.content)) {
                const textContent = result.content.find(c => c.type === 'text');
                if (textContent && textContent.text) {
                    try {
                        const parsed = JSON.parse(textContent.text);
                        if (parsed.events && Array.isArray(parsed.events)) {
                            events = parsed.events;
                        }
                    } catch (jsonError) {
                        logger.warn('Could not parse log sample result as JSON');
                    }
                }
            }

            // Format the events
            return events.map(event => ({
                timestamp: event.timestamp || new Date().toISOString(),
                message: event.message || String(event),
                logStreamName: event.streamName || event.logStreamName || ''
            }));
        } catch (error) {
            logger.error(`Error parsing log sample result: ${error.message}`);
            return [];
        }
    }

    /**
     * Parse recent errors result from MCP resource response
     * @param {Object} result - MCP resource result
     * @returns {Array<Object>} List of formatted log events
     */
    _parseRecentErrorsResult(result) {
        try {
            let events = [];

            // Check for structuredContent first
            if (result.structuredContent) {
                const content = result.structuredContent;
                if (content.events && Array.isArray(content.events)) {
                    events = content.events;
                }
            }

            // Fallback to content array
            if (events.length === 0 && result.content && Array.isArray(result.content)) {
                const textContent = result.content.find(c => c.type === 'text');
                if (textContent && textContent.text) {
                    try {
                        const parsed = JSON.parse(textContent.text);
                        if (parsed.events && Array.isArray(parsed.events)) {
                            events = parsed.events;
                        }
                    } catch (jsonError) {
                        logger.warn('Could not parse recent errors result as JSON');
                    }
                }
            }

            // Format the events
            return events.map(event => ({
                timestamp: event.timestamp || new Date().toISOString(),
                message: event.message || String(event),
                logStreamName: event.logStreamName || ''
            }));
        } catch (error) {
            logger.error(`Error parsing recent errors result: ${error.message}`);
            return [];
        }
    }

    /**
     * Parse analyze_log_group result from MCP tool response
     * @param {Object} result - MCP tool result
     * @param {number} limit - Maximum number of events to return
     * @returns {Array<Object>} List of formatted log events
     */
    _parseAnalyzeLogGroupResult(result, limit = 100) {
        try {
            let events = [];

            // Check for structuredContent first
            if (result.structuredContent) {
                const content = result.structuredContent;
                
                // Extract log samples or events
                if (content.log_samples && Array.isArray(content.log_samples)) {
                    events = content.log_samples;
                } else if (content.error_patterns && Array.isArray(content.error_patterns)) {
                    // If we have error patterns with examples
                    events = content.error_patterns.flatMap(pattern => 
                        pattern.examples || []
                    );
                } else if (content.anomalies && Array.isArray(content.anomalies)) {
                    // If we have anomalies
                    events = content.anomalies;
                }
            }

            // Fallback to content array
            if (events.length === 0 && result.content && Array.isArray(result.content)) {
                const textContent = result.content.find(c => c.type === 'text');
                if (textContent && textContent.text) {
                    try {
                        const parsed = JSON.parse(textContent.text);
                        if (parsed.log_samples) {
                            events = parsed.log_samples;
                        } else if (parsed.events) {
                            events = parsed.events;
                        } else if (Array.isArray(parsed)) {
                            events = parsed;
                        }
                    } catch (jsonError) {
                        // Text content might not be JSON
                        logger.warn('Could not parse log analysis result as JSON');
                    }
                }
            }

            // Format the events
            return events.slice(0, limit).map(event => ({
                timestamp: event.timestamp || event.time || new Date().toISOString(),
                message: event.message || event.log_message || String(event),
                logStreamName: event.logStreamName || event.log_stream || ''
            }));
        } catch (error) {
            logger.error(`Error parsing analyze log group result: ${error.message}`);
            return [];
        }
    }

    /**
     * Extract query ID from execute_log_insights_query result
     * @param {Object} result - MCP tool result
     * @returns {string|null} Query ID
     */
    _extractQueryId(result) {
        try {
            if (result.structuredContent && result.structuredContent.query_id) {
                return result.structuredContent.query_id;
            }
            if (result.content && Array.isArray(result.content)) {
                const textContent = result.content.find(c => c.type === 'text');
                if (textContent && textContent.text) {
                    const parsed = JSON.parse(textContent.text);
                    return parsed.query_id || parsed.queryId;
                }
            }
            return null;
        } catch (error) {
            logger.error(`Error extracting query ID: ${error.message}`);
            return null;
        }
    }

    /**
     * Extract query status from get_logs_insight_query_results result
     * @param {Object} result - MCP tool result
     * @returns {string} Query status
     */
    _extractQueryStatus(result) {
        try {
            if (result.structuredContent && result.structuredContent.status) {
                return result.structuredContent.status;
            }
            if (result.content && Array.isArray(result.content)) {
                const textContent = result.content.find(c => c.type === 'text');
                if (textContent && textContent.text) {
                    const parsed = JSON.parse(textContent.text);
                    return parsed.status || 'Unknown';
                }
            }
            return 'Unknown';
        } catch (error) {
            logger.error(`Error extracting query status: ${error.message}`);
            return 'Unknown';
        }
    }

    /**
     * Parse Logs Insights query results
     * @param {Object} result - MCP tool result
     * @returns {Array<Object>} Query results
     */
    _parseLogsInsightsResults(result) {
        try {
            let results = [];

            if (result.structuredContent && result.structuredContent.results) {
                results = result.structuredContent.results;
            } else if (result.content && Array.isArray(result.content)) {
                const textContent = result.content.find(c => c.type === 'text');
                if (textContent && textContent.text) {
                    const parsed = JSON.parse(textContent.text);
                    results = parsed.results || [];
                }
            }

            // CloudWatch Logs Insights returns results as an array of arrays
            // Each result is an array of field objects: [{ field: '@timestamp', value: '...' }, ...]
            // Convert to our standard format
            return results.map(resultRow => {
                const event = {};
                
                if (Array.isArray(resultRow)) {
                    // Standard Logs Insights format
                    resultRow.forEach(field => {
                        if (field.field === '@timestamp') {
                            event.timestamp = field.value;
                        } else if (field.field === '@message') {
                            event.message = field.value;
                        } else if (field.field === '@logStream') {
                            event.logStreamName = field.value;
                        } else {
                            // Store any other fields
                            event[field.field] = field.value;
                        }
                    });
                } else if (typeof resultRow === 'object') {
                    // Already in object format
                    event.timestamp = resultRow['@timestamp'] || resultRow.timestamp;
                    event.message = resultRow['@message'] || resultRow.message;
                    event.logStreamName = resultRow['@logStream'] || resultRow.logStreamName;
                }

                return event;
            });
        } catch (error) {
            logger.error(`Error parsing Logs Insights results: ${error.message}`);
            return [];
        }
    }
}

/**
 * Tool: List CloudWatch Log Groups
 * @returns {Promise<Object>} Tool result with log groups
 */
export async function listCloudWatchLogGroups() {
    const client = new CloudWatchClient();
    try {
        const logGroups = await client.listLogGroups();
        return {
            success: true,
            logGroups: logGroups,
            count: logGroups.length
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    } finally {
        await client.disconnect();
    }
}

/**
 * Tool: Get CloudWatch Logs
 * @param {Object} options - Query options
 * @returns {Promise<Object>} Tool result with logs
 */
export async function getCloudWatchLogs(options) {
    const client = new CloudWatchClient();
    try {
        const logs = await client.getLogs(options);
        
        if (logs.length === 0) {
            return {
                success: true,
                status: 'NO_LOGS_FOUND',
                message: `No logs found for ${options.logGroupName} in the past ${options.hoursAgo || 1} hours`,
                logs: []
            };
        }

        return {
            success: true,
            logs: logs,
            count: logs.length
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    } finally {
        await client.disconnect();
    }
}

/**
 * Tool: Analyze Logs for Errors
 * @param {Array<Object>} logs - Log events to analyze
 * @returns {Object} Analysis result
 */
export function analyzeLogsForErrors(logs) {
    // This is a placeholder for the agent to use its reasoning capabilities
    // The agent will parse the logs and identify errors based on patterns
    
    const errorPatterns = [
        /error/i,
        /exception/i,
        /failed/i,
        /fatal/i,
        /critical/i,
        /timeout/i,
        /refused/i
    ];

    const errors = logs.filter(log => {
        return errorPatterns.some(pattern => pattern.test(log.message));
    });

    return {
        totalLogs: logs.length,
        errorsFound: errors.length,
        errors: errors,
        hasErrors: errors.length > 0
    };
}

/**
 * Quick helper function to get logs from a specific log group
 * @param {string} logGroupName - Name of the log group
 * @param {Object} options - Optional parameters
 * @returns {Promise<Object>} Log results
 */
export async function queryLogGroup(logGroupName, options = {}) {
    const {
        hoursAgo = 1,
        filterPattern = '',
        limit = 100,
        includeAnalysis = true
    } = options;

    const client = new CloudWatchClient();
    try {
        const logs = await client.getLogs({
            logGroupName,
            hoursAgo,
            filterPattern,
            limit
        });

        const result = {
            logGroupName,
            timeRange: {
                hoursAgo,
                from: new Date(Date.now() - hoursAgo * 60 * 60 * 1000).toISOString(),
                to: new Date().toISOString()
            },
            filterPattern: filterPattern || '(none)',
            totalLogs: logs.length,
            logs: logs
        };

        // Add error analysis if requested
        if (includeAnalysis && logs.length > 0) {
            result.analysis = analyzeLogsForErrors(logs);
        }

        return result;
    } finally {
        await client.disconnect();
    }
}

// Export the client class for direct use
export { CloudWatchClient };
