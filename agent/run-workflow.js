import { WorkflowParser } from './src/worker/workflow-parser.js';
import { createOrchestratorExecutor } from './src/worker/orchestrator-executor.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const WORKFLOWS_FILE = path.join(__dirname, 'data', 'config', 'workflows.json');
const OUTPUT_DIR = path.join(__dirname, 'output');

async function runWorkflow(workflowName) {
    try {
        console.log(`Executing workflow: ${workflowName}`);
        
        // Load workflow
        const parser = new WorkflowParser(WORKFLOWS_FILE);
        const workflow = parser.loadWorkflow(workflowName);
        
        // Parse workflow graph
        const executionGraph = parser.parseWorkflow(workflow);
        
        // Execute workflow
        const executor = createOrchestratorExecutor(executionGraph);
        const result = await executor.execute();
        
        // Save output
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
    }            // Parse result if it's a string and add only the data
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
        
        fs.writeFileSync(filePath, JSON.stringify(output, null, 2));
        console.log(`Output saved: ${fileName}`);
        
        // Also save as latest
        const latestPath = path.join(OUTPUT_DIR, `${workflowName.replace(/\s+/g, '_')}_latest.json`);
        fs.writeFileSync(latestPath, JSON.stringify(output, null, 2));
        
        console.log('Success:', result.success);
        process.exit(result.success ? 0 : 1);
    } catch (error) {
        console.error('Failed:', error);
        process.exit(1);
    }
}

runWorkflow('daily-report');
