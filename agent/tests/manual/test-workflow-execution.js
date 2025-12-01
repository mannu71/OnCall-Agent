import { WorkflowParser } from '../../src/worker/workflow-parser.js';
import { createOrchestratorExecutor } from '../../src/worker/orchestrator-executor.js';
import logger from '../../src/shared/logger.js';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Manual test for workflow execution
 * This tests the full workflow execution without requiring the scheduler
 */
async function testWorkflowExecution() {
    try {
        console.log('='.repeat(60));
        console.log('Testing Workflow Execution');
        console.log('='.repeat(60));

        // Path to workflows file
        const workflowsFile = path.join(__dirname, '..', '..', 'src', 'workflows', 'workflows.json');

        console.log(`\nLoading workflow from: ${workflowsFile}`);

        // Create workflow parser
        const parser = new WorkflowParser(workflowsFile);

        // Load the "daily-report" workflow
        console.log('\n1. Loading workflow...');
        const workflow = parser.loadWorkflow('daily-report');
        console.log(`   ✓ Loaded workflow: ${workflow.name}`);

        // Parse workflow graph
        console.log('\n2. Parsing workflow graph...');
        const executionGraph = parser.parseWorkflow(workflow);
        console.log(`   ✓ Found orchestrator: ${executionGraph.orchestrator.data.label}`);
        console.log(`   ✓ Found ${executionGraph.tools.length} tools:`);
        executionGraph.tools.forEach(tool => {
            console.log(`     - ${tool.data.label}`);
        });
        console.log(`   ✓ Output node: ${executionGraph.output ? 'Yes' : 'No'}`);

        // Validate workflow
        console.log('\n3. Validating workflow...');
        parser.validateWorkflow(executionGraph);
        console.log('   ✓ Workflow validation passed');

        // Execute workflow
        console.log('\n4. Executing workflow...');
        console.log('   (This will connect to MCP servers and run queries)');
        console.log('');

        const executor = createOrchestratorExecutor(executionGraph);
        const result = await executor.execute();

        // Display results
        console.log('\n' + '='.repeat(60));
        console.log('Execution Results');
        console.log('='.repeat(60));
        console.log(`\nSuccess: ${result.success ? '✓ Yes' : '✗ No'}`);

        if (result.success) {
            console.log('\nOutput:');
            console.log('-'.repeat(60));
            console.log(result.output);
        } else {
            console.log(`\nError: ${result.error}`);
        }

        console.log('\n' + '='.repeat(60));
        console.log('Test completed');
        console.log('='.repeat(60));

        process.exit(result.success ? 0 : 1);

    } catch (error) {
        console.error('\n❌ Test failed:', error.message);
        console.error(error.stack);
        process.exit(1);
    }
}

// Run the test
testWorkflowExecution();
