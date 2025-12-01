import logger from '../shared/logger.js';

/**
 * Base class for workflow format parsers
 */
class WorkflowFormatParser {
    /**
     * Parse workflow definition and return steps
     * @param {string} definition - Workflow definition content
     * @param {Object} context - Execution context (tools, variables, etc.)
     * @returns {Array} Array of workflow steps
     */
    parse(definition, context) {
        throw new Error('parse() must be implemented by subclass');
    }

    /**
     * Check if this parser can handle the given definition
     * @param {string} definition - Workflow definition content
     * @returns {boolean}
     */
    canParse(definition) {
        throw new Error('canParse() must be implemented by subclass');
    }
}

/**
 * JSON workflow format parser
 * Format: { "steps": [ { "name": "...", "tool": "...", "server": "...", "params": {} } ] }
 */
export class JSONWorkflowParser extends WorkflowFormatParser {
    canParse(definition) {
        try {
            const parsed = JSON.parse(definition);
            return parsed && Array.isArray(parsed.steps);
        } catch {
            return false;
        }
    }

    parse(definition, context) {
        try {
            const workflow = JSON.parse(definition);

            if (!workflow.steps || !Array.isArray(workflow.steps)) {
                throw new Error('Invalid JSON workflow: missing "steps" array');
            }

            logger.info(`Parsed JSON workflow with ${workflow.steps.length} steps`);
            return workflow.steps;
        } catch (error) {
            logger.error('Failed to parse JSON workflow:', error);
            throw error;
        }
    }
}

/**
 * SQL workflow format parser
 * Format: SQL queries separated by semicolons or comments
 */
export class SQLWorkflowParser extends WorkflowFormatParser {
    canParse(definition) {
        // Simple heuristic: check if it looks like SQL
        const sqlKeywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER'];
        const upperDef = definition.trim().toUpperCase();
        return sqlKeywords.some(keyword => upperDef.includes(keyword));
    }

    parse(definition, context) {
        try {
            // Split by semicolons and filter out empty queries
            const queries = definition
                .split(';')
                .map(q => q.trim())
                .filter(q => q.length > 0 && !q.startsWith('--'));

            // Convert SQL queries to workflow steps
            const steps = queries.map((query, index) => {
                // Extract step name from comment if present
                const commentMatch = query.match(/--\s*Step\s+(\d+):\s*(.+)/i);
                const stepName = commentMatch ? commentMatch[2].trim() : `query_${index + 1}`;

                // Determine which server to use (default to first PostgreSQL server)
                const postgresServer = context.tools.find(
                    t => t.data.toolType === 'mcp-server' &&
                        t.data.mcpConfig.command === 'npx' &&
                        t.data.mcpConfig.args?.some(arg => arg.includes('server-postgres'))
                );

                if (!postgresServer) {
                    throw new Error('No PostgreSQL MCP server found for SQL workflow');
                }

                return {
                    name: stepName,
                    tool: 'query',
                    server: postgresServer.id,
                    params: {
                        sql: query
                    }
                };
            });

            logger.info(`Parsed SQL workflow with ${steps.length} queries`);
            return steps;
        } catch (error) {
            logger.error('Failed to parse SQL workflow:', error);
            throw error;
        }
    }
}

/**
 * Workflow format parser registry
 */
export class WorkflowParserRegistry {
    constructor() {
        this.parsers = [
            new JSONWorkflowParser(),
            new SQLWorkflowParser()
        ];
    }

    /**
     * Find appropriate parser for workflow definition
     */
    findParser(definition) {
        for (const parser of this.parsers) {
            if (parser.canParse(definition)) {
                return parser;
            }
        }
        throw new Error('No parser found for workflow definition');
    }

    /**
     * Parse workflow definition using appropriate parser
     */
    parse(definition, context) {
        const parser = this.findParser(definition);
        logger.info(`Using parser: ${parser.constructor.name}`);
        return parser.parse(definition, context);
    }

    /**
     * Register a custom parser
     */
    registerParser(parser) {
        if (!(parser instanceof WorkflowFormatParser)) {
            throw new Error('Parser must extend WorkflowFormatParser');
        }
        this.parsers.unshift(parser); // Add to beginning for priority
        logger.info(`Registered custom parser: ${parser.constructor.name}`);
    }
}
