/**
 * LangChain-compatible CloudWatch Tools
 * 
 * These tools integrate CloudWatch Logs functionality into LangGraph workflows.
 */

import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { 
  queryLogGroup, 
  listCloudWatchLogGroups 
} from './cloudwatch-tools.js';
import logger from '../../shared/logger.js';

/**
 * LangChain tool for querying CloudWatch logs
 */
export const queryCloudWatchLogsTool = tool(
  async ({ logGroupName, hoursAgo = 1, filterPattern = '' }) => {
    try {
      logger.info(`CloudWatch tool called: query_cloudwatch_logs`, {
        logGroupName,
        hoursAgo,
        filterPattern
      });

      const result = await queryLogGroup(logGroupName, {
        hoursAgo,
        filterPattern,
        limit: 100,
        includeAnalysis: true
      });

      // Format response for LLM
      const response = {
        success: true,
        logGroup: result.logGroupName,
        timeRange: {
          description: `Last ${hoursAgo} hour(s)`,
          from: result.timeRange.from,
          to: result.timeRange.to
        },
        totalLogs: result.totalLogs,
        filterPattern: result.filterPattern
      };

      // Include error analysis if available
      if (result.analysis) {
        response.errorAnalysis = {
          hasErrors: result.analysis.hasErrors,
          errorCount: result.analysis.errorsFound,
          totalLogs: result.analysis.totalLogs
        };

        // Include sample errors (first 5)
        if (result.analysis.hasErrors && result.analysis.errors.length > 0) {
          response.errorAnalysis.sampleErrors = result.analysis.errors.slice(0, 5).map(err => ({
            timestamp: err.timestamp,
            message: err.message.substring(0, 300), // Limit message length
            logStream: err.logStreamName
          }));
        }
      }

      // Include sample logs (first 5) if no errors or if requested
      if (result.logs.length > 0 && (!result.analysis?.hasErrors || filterPattern)) {
        response.sampleLogs = result.logs.slice(0, 5).map(log => ({
          timestamp: log.timestamp,
          message: log.message.substring(0, 300), // Limit message length
          logStream: log.logStreamName
        }));
      }

      // Add summary message
      if (result.totalLogs === 0) {
        response.summary = `No logs found in ${logGroupName} for the specified time range and filter.`;
      } else if (result.analysis?.hasErrors) {
        response.summary = `Found ${result.totalLogs} logs with ${result.analysis.errorsFound} errors (${Math.round(result.analysis.errorsFound / result.totalLogs * 100)}% error rate).`;
      } else {
        response.summary = `Found ${result.totalLogs} logs with no errors detected.`;
      }

      return JSON.stringify(response, null, 2);
    } catch (error) {
      logger.error('CloudWatch tool error:', error);
      return JSON.stringify({ 
        success: false,
        error: true, 
        message: error.message,
        logGroupName
      });
    }
  },
  {
    name: "query_cloudwatch_logs",
    description: `Query AWS CloudWatch logs for a specific log group. 
Use this to investigate errors, check application logs, or monitor Lambda functions.
Returns logs with automatic error analysis.

Use cases:
- Investigate Lambda function errors
- Check API Gateway logs
- Monitor application health
- Search for specific error patterns
- Analyze recent activity

Parameters:
- logGroupName: The CloudWatch log group name (e.g., '/aws/lambda/my-function')
- hoursAgo: Number of hours to look back (default: 1, max: 24)
- filterPattern: Optional text to filter logs by (e.g., 'ERROR', 'timeout', 'exception')

Returns:
- Summary of logs found
- Error analysis with counts and samples
- Sample log entries
- Time range information`,
    schema: z.object({
      logGroupName: z.string().describe("The CloudWatch log group name (e.g., '/aws/lambda/function-name')"),
      hoursAgo: z.number().optional().default(1).describe("Hours to look back (1-24). Default: 1"),
      filterPattern: z.string().optional().default('').describe("Optional text to filter logs by (e.g., 'ERROR', 'timeout')")
    })
  }
);

/**
 * LangChain tool for listing CloudWatch log groups
 */
export const listCloudWatchLogGroupsTool = tool(
  async () => {
    try {
      logger.info('CloudWatch tool called: list_cloudwatch_log_groups');

      const result = await listCloudWatchLogGroups();
      
      if (!result.success) {
        return JSON.stringify({ 
          success: false,
          error: true, 
          message: result.error 
        });
      }

      const response = {
        success: true,
        count: result.count,
        logGroups: result.logGroups.slice(0, 50), // Limit to first 50
        summary: `Found ${result.count} CloudWatch log groups in the configured region.`
      };

      // Group by service type for better organization
      const grouped = {
        lambda: [],
        apiGateway: [],
        ecs: [],
        other: []
      };

      result.logGroups.forEach(name => {
        if (name.startsWith('/aws/lambda/')) {
          grouped.lambda.push(name);
        } else if (name.startsWith('/aws/apigateway/')) {
          grouped.apiGateway.push(name);
        } else if (name.startsWith('/ecs/')) {
          grouped.ecs.push(name);
        } else {
          grouped.other.push(name);
        }
      });

      response.groupedByService = {
        lambda: { count: grouped.lambda.length, examples: grouped.lambda.slice(0, 10) },
        apiGateway: { count: grouped.apiGateway.length, examples: grouped.apiGateway.slice(0, 10) },
        ecs: { count: grouped.ecs.length, examples: grouped.ecs.slice(0, 10) },
        other: { count: grouped.other.length, examples: grouped.other.slice(0, 10) }
      };

      return JSON.stringify(response, null, 2);
    } catch (error) {
      logger.error('CloudWatch tool error:', error);
      return JSON.stringify({ 
        success: false,
        error: true, 
        message: error.message 
      });
    }
  },
  {
    name: "list_cloudwatch_log_groups",
    description: `List available AWS CloudWatch log groups in the configured region.
Use this to discover what log groups are available before querying them.

Use cases:
- Discover available Lambda functions
- Find API Gateway logs
- List ECS service logs
- Identify log groups for monitoring

Returns:
- Total count of log groups
- List of log group names (up to 50)
- Groups organized by service type (Lambda, API Gateway, ECS, etc.)
- Examples from each category`,
    schema: z.object({})
  }
);

/**
 * Export all CloudWatch tools as an array for easy integration
 */
export const cloudWatchTools = [
  queryCloudWatchLogsTool,
  listCloudWatchLogGroupsTool
];

/**
 * Get CloudWatch tools with metadata
 */
export function getCloudWatchTools() {
  return {
    tools: cloudWatchTools,
    count: cloudWatchTools.length,
    names: cloudWatchTools.map(t => t.name),
    description: 'AWS CloudWatch Logs integration tools for querying and analyzing logs'
  };
}
