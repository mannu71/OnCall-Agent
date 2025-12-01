import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import logger from '../shared/logger.js';
import { WorkflowParser } from './workflow-parser.js';
import { createOrchestratorExecutor} from './orchestrator-executor.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const WORKFLOWS_FILE = process.env.WORKFLOWS_FILE || path.join(__dirname, '..', '..', 'data', 'config', 'workflows.json');
const TRIGGER_DIR = process.env.TRIGGER_DIR || path.join(__dirname, '..', '..', 'data', 'config', 'triggers');
const OUTPUT_DIR = process.env.OUTPUT_DIR || path.join(__dirname, '..', '..', 'output');

/**
 * Main worker service
 */
class WorkerService {
    constructor() {
        this.workflowParser = new WorkflowParser(WORKFLOWS_FILE);
        this.watcher = null;
        this.isProcessing = false;
        this.pendingTriggers = [];
        this.pollingInterval = null;
        this.isShuttingDown = false;
    }

    /**
     * Start the worker service
     */
    start() {
        logger.info('Starting Orchestrator Worker Service');
        logger.info(`Workflows file: ${WORKFLOWS_FILE}`);
        logger.info(`Trigger directory: ${TRIGGER_DIR}`);
        logger.info(`Output directory: ${OUTPUT_DIR}`);

        // Ensure directories exist
        this.ensureDirectories();

        // Start watching for trigger files
        this.startWatching();

        logger.info('Worker service started successfully');
    }

    /**
     * Ensure required directories exist
     */
    ensureDirectories() {
        if (!fs.existsSync(TRIGGER_DIR)) {
            fs.mkdirSync(TRIGGER_DIR, { recursive: true });
            logger.info(`Created trigger directory: ${TRIGGER_DIR}`);
        }

        if (!fs.existsSync(OUTPUT_DIR)) {
            fs.mkdirSync(OUTPUT_DIR, { recursive: true });
            logger.info(`Created output directory: ${OUTPUT_DIR}`);
        }
    }

    /**
     * Start watching trigger directory
     */
    startWatching() {
        logger.info('Starting trigger file polling');
        logger.info(`Polling directory: ${TRIGGER_DIR}`);

        // Poll for trigger files every 2 seconds
        this.pollingInterval = setInterval(() => {
            this.checkForTriggers();
        }, 2000);

        logger.info('Trigger file polling started');
    }

    /**
     * Check for trigger files in directory
     */
    async checkForTriggers() {
        try {
            if (!fs.existsSync(TRIGGER_DIR)) {
                return;
            }

            const files = fs.readdirSync(TRIGGER_DIR);
            const triggerFiles = files.filter(f => f.endsWith('.flag'));

            for (const file of triggerFiles) {
                const filePath = path.join(TRIGGER_DIR, file);
                await this.handleTriggerFile(filePath);
            }
        } catch (error) {
            logger.error('Error checking for triggers:', error);
        }
    }

    /**
     * Handle trigger file
     */
    async handleTriggerFile(filePath) {
        // Check if file still exists before processing
        if (!fs.existsSync(filePath)) {
            logger.debug(`Trigger file already processed: ${filePath}`);
            return;
        }

        if (this.isProcessing) {
            // Queue the trigger instead of dropping it
            if (!this.pendingTriggers.includes(filePath)) {
                this.pendingTriggers.push(filePath);
                logger.info(`Queued trigger for later processing: ${filePath}`);
            }
            return;
        }

        try {
            this.isProcessing = true;

            const fileName = path.basename(filePath, '.flag');
            logger.info(`Trigger detected: ${fileName}`);

            // Delete trigger file first to prevent re-processing
            try {
                fs.unlinkSync(filePath);
                logger.debug(`Trigger file deleted: ${fileName}`);
            } catch (unlinkError) {
                if (unlinkError.code !== 'ENOENT') {
                    throw unlinkError;
                }
                // File already deleted, continue processing
            }

            // Extract workflow name from filename
            const workflowName = fileName.replace(/_/g, ' ');

            // Execute workflow
            await this.executeWorkflow(workflowName);

            logger.info(`Trigger file processed: ${fileName}`);

        } catch (error) {
            logger.error('Error handling trigger file:', error);
        } finally {
            this.isProcessing = false;
            // Process next queued trigger if any
            await this.processNextQueuedTrigger();
        }
    }

    /**
     * Process the next queued trigger
     */
    async processNextQueuedTrigger() {
        if (this.isShuttingDown || this.pendingTriggers.length === 0) {
            return;
        }
        const nextTrigger = this.pendingTriggers.shift();
        if (nextTrigger && fs.existsSync(nextTrigger)) {
            await this.handleTriggerFile(nextTrigger);
        } else if (this.pendingTriggers.length > 0) {
            // Skip non-existent files and try next
            await this.processNextQueuedTrigger();
        }
    }

    /**
     * Execute a workflow
     */
    async executeWorkflow(workflowName) {
        try {
            logger.info(`Executing workflow: ${workflowName}`);

            // Load workflow
            const workflow = this.workflowParser.loadWorkflow(workflowName);

            // Parse workflow graph
            const executionGraph = this.workflowParser.parseWorkflow(workflow);

            // Validate workflow
            this.workflowParser.validateWorkflow(executionGraph);

            // Execute workflow
            const executor = createOrchestratorExecutor(executionGraph);
            const result = await executor.execute();

            // Save output
            await this.saveOutput(workflowName, result);

            if (result.success) {
                logger.info(`Workflow executed successfully: ${workflowName}`);
            } else {
                // Log error as object to prevent Winston splat from spreading string characters
                logger.error(`Workflow execution failed: ${workflowName}`, { error: result.error });
            }

            return result;

        } catch (error) {
            logger.error(`Failed to execute workflow ${workflowName}:`, error);
            throw error;
        }
    }

    /**
     * Save workflow output
     */
    async saveOutput(workflowName, result) {
        try {
            const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
            const fileName = `${workflowName.replace(/\s+/g, '_')}_${timestamp}.json`;
            const filePath = path.join(OUTPUT_DIR, fileName);

            // Transform results to include label at top level and only query result
            const transformedResults = result.results?.map(r => {
                const transformed = {
                    step: r.step,
                    success: r.success
                };

                // Add label if it exists in metadata
                if (r.metadata?.label) {
                    transformed.label = r.metadata.label;
                }

                // Add database name if it exists in metadata
                if (r.metadata?.database) {
                    transformed.database = r.metadata.database;
                }

                // Parse result if it's a string and add only the data
                if (r.success && r.result) {
                    try {
                        const parsedResult = typeof r.result === 'string' ? JSON.parse(r.result) : r.result;
                        transformed.result = parsedResult;
                    } catch {
                        transformed.result = r.result;
                    }
                } else if (!r.success) {
                    transformed.error = r.error;
                }

                return transformed;
            }) || [];

            const output = {
                workflow: workflowName,
                timestamp: new Date().toISOString(),
                success: result.success,
                results: transformedResults,
                error: result.error
            };

            const outputJson = JSON.stringify(output, null, 2);
            await Promise.all([
                fs.promises.writeFile(filePath, outputJson),
                fs.promises.writeFile(
                    path.join(OUTPUT_DIR, `${workflowName.replace(/\s+/g, '_')}_latest.json`),
                    outputJson
                )
            ]);
            logger.info(`Output saved: ${fileName}`);

        } catch (error) {
            logger.error('Failed to save output:', error);
        }
    }

    /**
     * Stop the worker service
     */
    async stop() {
        logger.info('Stopping worker service');
        this.isShuttingDown = true;
        this.pendingTriggers = [];

        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }

        logger.info('Worker service stopped');
    }
}

// Start the worker service
const worker = new WorkerService();
worker.start();

// Handle graceful shutdown
process.on('SIGINT', async () => {
    logger.info('Received SIGINT, shutting down gracefully');
    await worker.stop();
    process.exit(0);
});

process.on('SIGTERM', async () => {
    logger.info('Received SIGTERM, shutting down gracefully');
    await worker.stop();
    process.exit(0);
});

export default WorkerService;
