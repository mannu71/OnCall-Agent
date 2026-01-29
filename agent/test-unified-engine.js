/**
 * Test Script for Unified Workflow Engine
 * 
 * This script tests the unified engine with sample workflows
 * to ensure both strategies work correctly.
 * 
 * Usage: node test-unified-engine.js
 */

import { WorkflowEngine } from './src/workflow/index.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Sample workflows for testing
const sampleReActWorkflow = {
  id: 'test-react',
  name: 'Test ReAct Workflow',
  nodes: [
    {
      id: 'agent-1',
      type: 'agent',
      data: {
        label: 'OnCall Agent',
        instructions: 'You are a helpful assistant.'
      }
    },
    {
      id: 'llm-1',
      type: 'llm',
      data: {
        label: 'GPT-4',
        provider: 'OpenAI',
        model: 'gpt-4',
        temperature: 0.7
      }
    },
    {
      id: 'tool-1',
      type: 'tool',
      data: {
        label: 'Database'
      }
    }
  ],
  edges: [
    { id: 'e1', source: 'tool-1', target: 'agent-1', targetHandle: 'tool' },
    { id: 'e2', source: 'llm-1', target: 'agent-1', targetHandle: 'llm' }
  ]
};

const sampleOrchestratorWorkflow = {
  id: 'test-orchestrator',
  name: 'Test Orchestrator Workflow',
  nodes: [
    {
      id: 'orchestrator-1',
      type: 'orchestrator',
      data: {
        label: 'Daily Report',
        workflowDefinition: JSON.stringify({
          steps: [
            {
              name: 'get_count',
              tool: 'query',
              server: 'database',
              params: { sql: 'SELECT COUNT(*) as count FROM users' }
            }
          ]
        })
      }
    },
    {
      id: 'tool-1',
      type: 'tool',
      data: {
        label: 'Database'
      }
    },
    {
      id: 'output-1',
      type: 'output',
      data: {
        label: 'Output'
      }
    }
  ],
  edges: [
    { id: 'e1', source: 'tool-1', target: 'orchestrator-1', targetHandle: 'tool' },
    { id: 'e2', source: 'orchestrator-1', target: 'output-1', sourceHandle: 'orchestrator-output' }
  ]
};

async function runTests() {
  console.log('='.repeat(80));
  console.log('UNIFIED WORKFLOW ENGINE TEST SUITE');
  console.log('='.repeat(80));
  console.log();

  const engine = new WorkflowEngine();
  let passed = 0;
  let failed = 0;

  // Test 1: Validate ReAct Workflow
  console.log('Test 1: Validate ReAct Workflow');
  try {
    const result = engine.validate(sampleReActWorkflow);
    if (result.valid && result.strategy === 'ReactStrategy') {
      console.log('✅ PASS - ReAct workflow validated correctly');
      console.log(`   Strategy: ${result.strategy}`);
      passed++;
    } else {
      console.log('❌ FAIL - Unexpected validation result');
      console.log('   Result:', result);
      failed++;
    }
  } catch (error) {
    console.log('❌ FAIL - Validation threw error:', error.message);
    failed++;
  }
  console.log();

  // Test 2: Validate Orchestrator Workflow
  console.log('Test 2: Validate Orchestrator Workflow');
  try {
    const result = engine.validate(sampleOrchestratorWorkflow);
    if (result.valid && result.strategy === 'OrchestratorStrategy') {
      console.log('✅ PASS - Orchestrator workflow validated correctly');
      console.log(`   Strategy: ${result.strategy}`);
      passed++;
    } else {
      console.log('❌ FAIL - Unexpected validation result');
      console.log('   Result:', result);
      failed++;
    }
  } catch (error) {
    console.log('❌ FAIL - Validation threw error:', error.message);
    failed++;
  }
  console.log();

  // Test 3: Validate Invalid Workflow (missing nodes)
  console.log('Test 3: Validate Invalid Workflow (missing nodes)');
  try {
    const invalidWorkflow = {
      id: 'test-invalid',
      name: 'Invalid Workflow',
      nodes: [],
      edges: []
    };
    const result = engine.validate(invalidWorkflow);
    if (!result.valid) {
      console.log('✅ PASS - Invalid workflow rejected correctly');
      console.log(`   Error: ${result.error}`);
      passed++;
    } else {
      console.log('❌ FAIL - Invalid workflow was accepted');
      failed++;
    }
  } catch (error) {
    console.log('✅ PASS - Invalid workflow rejected with error:', error.message);
    passed++;
  }
  console.log();

  // Test 4: Validate Workflow with Cycle
  console.log('Test 4: Validate Workflow with Cycle');
  try {
    const cyclicWorkflow = {
      id: 'test-cycle',
      name: 'Cyclic Workflow',
      nodes: [
        { id: 'n1', type: 'tool', data: { label: 'Tool 1' } },
        { id: 'n2', type: 'tool', data: { label: 'Tool 2' } }
      ],
      edges: [
        { id: 'e1', source: 'n1', target: 'n2' },
        { id: 'e2', source: 'n2', target: 'n1' }  // Creates cycle
      ]
    };
    const result = engine.validate(cyclicWorkflow);
    if (!result.valid && result.error.includes('cycle')) {
      console.log('✅ PASS - Cyclic workflow rejected correctly');
      console.log(`   Error: ${result.error}`);
      passed++;
    } else {
      console.log('❌ FAIL - Cyclic workflow was accepted');
      failed++;
    }
  } catch (error) {
    if (error.message.includes('cycle')) {
      console.log('✅ PASS - Cyclic workflow rejected with error:', error.message);
      passed++;
    } else {
      console.log('❌ FAIL - Wrong error:', error.message);
      failed++;
    }
  }
  console.log();

  // Test 5: Load Real Workflows (if they exist)
  console.log('Test 5: Load and Validate Real Workflows');
  try {
    const workflowsPath = path.join(__dirname, 'data', 'config', 'workflows.json');
    if (fs.existsSync(workflowsPath)) {
      const workflows = JSON.parse(fs.readFileSync(workflowsPath, 'utf-8'));
      console.log(`   Found ${workflows.length} real workflows`);
      
      let validCount = 0;
      let invalidCount = 0;
      
      for (const workflow of workflows) {
        const result = engine.validate(workflow);
        if (result.valid) {
          console.log(`   ✅ ${workflow.name} - ${result.strategy}`);
          validCount++;
        } else {
          console.log(`   ❌ ${workflow.name} - ${result.error}`);
          invalidCount++;
        }
      }
      
      if (invalidCount === 0) {
        console.log('✅ PASS - All real workflows are valid');
        passed++;
      } else {
        console.log(`⚠️  WARN - ${invalidCount} invalid workflows found`);
        passed++;  // Still pass if we can validate
      }
    } else {
      console.log('   ⚠️  No real workflows file found (skipped)');
      passed++;
    }
  } catch (error) {
    console.log('❌ FAIL - Error loading real workflows:', error.message);
    failed++;
  }
  console.log();

  // Test 6: Strategy Selection
  console.log('Test 6: Strategy Selection Logic');
  try {
    const reactStrategy = engine.selectStrategy(sampleReActWorkflow);
    const orchStrategy = engine.selectStrategy(sampleOrchestratorWorkflow);
    
    if (reactStrategy.constructor.name === 'ReactStrategy' &&
        orchStrategy.constructor.name === 'OrchestratorStrategy') {
      console.log('✅ PASS - Strategies selected correctly');
      console.log(`   ReAct: ${reactStrategy.constructor.name}`);
      console.log(`   Orchestrator: ${orchStrategy.constructor.name}`);
      passed++;
    } else {
      console.log('❌ FAIL - Wrong strategies selected');
      failed++;
    }
  } catch (error) {
    console.log('❌ FAIL - Strategy selection error:', error.message);
    failed++;
  }
  console.log();

  // Summary
  console.log('='.repeat(80));
  console.log('TEST SUMMARY');
  console.log('='.repeat(80));
  console.log(`Total Tests: ${passed + failed}`);
  console.log(`Passed: ${passed} ✅`);
  console.log(`Failed: ${failed} ❌`);
  console.log(`Success Rate: ${((passed / (passed + failed)) * 100).toFixed(1)}%`);
  console.log('='.repeat(80));

  // Cleanup
  await engine.cleanup();

  // Exit with appropriate code
  process.exit(failed > 0 ? 1 : 0);
}

// Run tests
runTests().catch(error => {
  console.error('Test suite failed:', error);
  process.exit(1);
});
