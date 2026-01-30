/**
 * Unified Worker Service
 * 
 * This is the NEW worker that uses the unified workflow engine.
 * It replaces the old worker which only supported orchestrator workflows.
 * 
 * This worker can execute ANY workflow type (ReAct or Orchestrator).
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import logger from '../shared/logger.js';
import { WorkflowEngine } from '../workflow/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const WORKFLOWS_FILE = process.env.WORKFLOWS_FILE || path.join(__dirname, '..', '..', 'data', 'config', 'workflows.json');
const TRIGGER_DIR = process.env.TRIGGER_DIR || path.join(__dirname, '..', '..', 'data', 'config', 'triggers');
const OUTPUT_DIR = process.env.OUTPUT_DIR || path.join(__dirname, '..', '..', 'output');

/**
 * Unified Worker Service
 */
class UnifiedWorkerService {
  constructor() {
    this.engine = new WorkflowEngine();
    this.isProcessing = false;
    this.pendingTriggers = [];
    this.pollingInterval = null;
    this.isShuttingDown = false;
  }

  /**
   * Start the worker service
   */
  start() {
    logger.info('Starting Unified Worker Service');
    logger.info('Workflows file:', WORKFLOWS_FILE);
    logger.info('Trigger directory:', TRIGGER_DIR);
    logger.info('Output directory:', OUTPUT_DIR);

    // Ensure directories exist
    this.ensureDirectories();

    // Start watching for trigger files
    this.startWatching();

    logger.info('Unified Worker Service started successfully');
  }

  /**
   * Ensure required directories exist
   */
  ensureDirectories() {
    if (!fs.existsSync(TRIGGER_DIR)) {
      fs.mkdirSync(TRIGGER_DIR, { recursive: true });
      logger.info('Created trigger directory:', TRIGGER_DIR);
    }

    if (!fs.existsSync(OUTPUT_DIR)) {
      fs.mkdirSync(OUTPUT_DIR, { recursive: true });
      logger.info('Created output directory:', OUTPUT_DIR);
    }
  }

  /**
   * Start watching trigger directory
   */
  startWatching() {
    logger.info('Starting trigger file polling');
    logger.info('Polling directory:', TRIGGER_DIR);

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
      logger.debug('Trigger file already processed:', filePath);
      return;
    }

    if (this.isProcessing) {
      // Queue the trigger instead of dropping it
      if (!this.pendingTriggers.includes(filePath)) {
        this.pendingTriggers.push(filePath);
        logger.info('Queued trigger for later processing:', filePath);
      }
      return;
    }

    try {
      this.isProcessing = true;

      const fileName = path.basename(filePath, '.flag');
      logger.info('Trigger detected:', fileName);

      // Delete trigger file first to prevent re-processing
      try {
        fs.unlinkSync(filePath);
        logger.debug('Trigger file deleted:', fileName);
      } catch (unlinkError) {
        if (unlinkError.code !== 'ENOENT') {
          throw unlinkError;
        }
      }

      // Extract workflow name from filename
      const workflowName = fileName.replace(/_/g, ' ');

      // Execute workflow using unified engine
      await this.executeWorkflow(workflowName);

      logger.info('Trigger file processed:', fileName);

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
   * Execute a workflow using the unified engine
   */
  async executeWorkflow(workflowName) {
    try {
      logger.info('Executing workflow:', workflowName);

      // Load workflow
      const workflow = this.loadWorkflow(workflowName);

      // Execute using unified engine
      const result = await this.engine.execute(workflow, {
        variables: {
          current_date: new Date().toISOString().split('T')[0]
        }
      });

      // Save output
      await this.saveOutput(workflowName, result);

      if (result.success) {
        logger.info('Workflow executed successfully:', workflowName);
      } else {
        logger.error('Workflow execution failed:', workflowName, {
          error: result.error
        });
      }

      return result;

    } catch (error) {
      logger.error(`Failed to execute workflow ${workflowName}:`, error);
      throw error;
    }
  }

  /**
   * Load workflow by name
   */
  loadWorkflow(workflowName) {
    const data = fs.readFileSync(WORKFLOWS_FILE, 'utf8');
    const workflows = JSON.parse(data);

    const workflow = workflows.find(
      w => w.id === workflowName || w.name === workflowName
    );

    if (!workflow) {
      throw new Error(`Workflow not found: ${workflowName}`);
    }

    logger.info('Loaded workflow:', workflow.name, `(${workflow.id})`);
    return workflow;
  }

  /**
   * Save workflow output
   */
  async saveOutput(workflowName, result) {
    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const fileName = `${workflowName.replace(/\s+/g, '_')}_${timestamp}.json`;
      const filePath = path.join(OUTPUT_DIR, fileName);

      const output = {
        workflow: workflowName,
        executionId: result.executionId,
        timestamp: result.timestamp,
        success: result.success,
        strategy: result.strategy,
        duration: result.duration,
        error: result.error
      };

      // For orchestrator workflows, include structured results array
      if (result.results && Array.isArray(result.results)) {
        output.results = result.results;
      }

      // Include the formatted output/answer
      if (result.finalAnswer || result.output) {
        output.result = result.finalAnswer || result.output;
      }

      const outputJson = JSON.stringify(output, null, 2);
      await Promise.all([
        fs.promises.writeFile(filePath, outputJson),
        fs.promises.writeFile(
          path.join(OUTPUT_DIR, `${workflowName.replace(/\s+/g, '_')}_latest.json`),
          outputJson
        )
      ]);
      logger.info('Output saved:', fileName);

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

    await this.engine.cleanup();

    logger.info('Worker service stopped');
  }
}

// Start the worker service
const worker = new UnifiedWorkerService();
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

export default UnifiedWorkerService;
